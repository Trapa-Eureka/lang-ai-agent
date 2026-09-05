# pyright: reportUnknownMemberType=false
# httpx's streaming helpers and Starlette's lifespan_context are typed
# loosely enough to trip this check on ordinary usage. Scoped to this file.
"""The FastAPI service (T7 completion criteria — docs/TESTING.md §4 "API·SSE",
driven through httpx's ASGI transport, no server process).

1. no / wrong auth -> 401
2. the messages stream's event order: token* -> tool_start/end* -> (interrupt | usage -> done)
3. after an interrupt, GET state exposes pending; approve then streams through to done
4. an unknown thread_id -> 404 with a fix in the message
5. audit 001 (T16): one run per thread at a time, request limits, thread deletion
"""

import asyncio
import json
import os
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from langchain_core.messages import AIMessage

from lang_ai_agent.adapters.checkpoint import thread_config
from lang_ai_agent.api.app import ConfigError, create_app, create_default_app
from lang_ai_agent.api.auth import require_bearer_token
from lang_ai_agent.api.limits import MAX_BODY_BYTES, MAX_MESSAGE_CHARS
from tests.component.conftest import GraphHarness
from tests.helpers.http_client import AUTH_HEADERS as AUTH
from tests.helpers.http_client import TEST_TOKEN as TOKEN
from tests.helpers.http_client import api_client as _client
from tests.helpers.http_client import kinds, request_sse, token_text
from tests.helpers.http_client import read_sse as _stream_events
from tests.helpers.scripted_chat_model import script

_SEND_EMAIL_SCRIPT = (
    script()
    .tool_call("check_stockout", {"store": "main"})
    .tool_call(
        "send_reorder_email", {"to": "ops@example.com", "subject": "Reorder", "body": "35 units"}
    )
    .final("Sent it.")
    .build()
)


def _app_for(script_messages: Sequence[AIMessage]) -> tuple[FastAPI, GraphHarness]:
    harness = GraphHarness(script_messages)

    @asynccontextmanager
    async def graph_factory() -> AsyncGenerator[Any, None]:
        yield harness.graph

    return create_app(graph_factory, bearer_token=TOKEN), harness


# --- 1. auth ----------------------------------------------------------------


async def test_missing_bearer_token_is_401_with_a_fix() -> None:
    app, _ = _app_for(_SEND_EMAIL_SCRIPT)
    async with _client(app) as client:
        response = await client.post("/threads")

    assert response.status_code == 401
    assert "Authorization: Bearer" in response.json()["detail"]


async def test_wrong_bearer_token_is_401() -> None:
    app, _ = _app_for(_SEND_EMAIL_SCRIPT)
    async with _client(app) as client:
        response = await client.post("/threads", headers={"Authorization": "Bearer nope"})

    assert response.status_code == 401


def test_an_empty_configured_token_is_refused_at_startup() -> None:
    with pytest.raises(ValueError, match="APP_BEARER_TOKEN is empty"):
        require_bearer_token("")


# --- 2. messages stream order ------------------------------------------------


async def test_messages_stream_ends_with_usage_then_done_when_nothing_needs_approval() -> None:
    app, harness = _app_for(
        script().tool_call("check_stockout", {"store": "main"}).final("All good here").build()
    )
    async with _client(app) as client:
        thread_id = (await client.post("/threads", headers=AUTH)).json()["thread_id"]
        events = await _stream_events(
            client, "POST", f"/threads/{thread_id}/messages", {"content": "what's at risk?"}
        )

    kinds = [e["event"] for e in events]
    assert kinds[:2] == ["tool_start", "tool_end"]
    assert kinds[-2:] == ["usage", "done"]
    assert set(kinds[2:-2]) == {"token"}
    assert "".join(e["data"]["content"] for e in events if e["event"] == "token") == "All good here"
    # the SSE `event:` field and the JSON `type` field agree, so a client
    # can dispatch on either
    assert all(e["event"] == e["data"]["type"] for e in events)
    harness.model.assert_exhausted()


# --- 3. interrupt -> state exposes pending -> approve streams to done -------


async def test_interrupt_then_state_shows_pending_then_approve_streams_to_done() -> None:
    app, harness = _app_for(_SEND_EMAIL_SCRIPT)
    async with _client(app) as client:
        thread_id = (await client.post("/threads", headers=AUTH)).json()["thread_id"]

        first = await _stream_events(
            client, "POST", f"/threads/{thread_id}/messages", {"content": "reorder please"}
        )
        assert [e["event"] for e in first][-1] == "interrupt"
        assert first[-1]["data"]["pending"]["tool_name"] == "send_reorder_email"
        assert first[-1]["data"]["draft"] == "35 units"

        state = (await client.get(f"/threads/{thread_id}/state", headers=AUTH)).json()
        assert state["awaiting_approval"] is True
        assert state["pending"]["tool_name"] == "send_reorder_email"
        assert state["message_count"] >= 2

        second = await _stream_events(
            client, "POST", f"/threads/{thread_id}/approve", {"approved": True}
        )
        kinds = [e["event"] for e in second]
        assert kinds[:2] == ["tool_start", "tool_end"]
        assert kinds[-2:] == ["usage", "done"]

        state_after = (await client.get(f"/threads/{thread_id}/state", headers=AUTH)).json()
        assert state_after["awaiting_approval"] is False
        assert state_after["pending"] is None
        assert state_after["last_message"] == "Sent it."

    assert harness.effects.send_email_calls  # approval actually ran the tool
    harness.model.assert_exhausted()


async def test_rejecting_streams_to_done_without_running_the_tool() -> None:
    app, harness = _app_for(_SEND_EMAIL_SCRIPT)
    async with _client(app) as client:
        thread_id = (await client.post("/threads", headers=AUTH)).json()["thread_id"]
        await _stream_events(
            client, "POST", f"/threads/{thread_id}/messages", {"content": "reorder please"}
        )
        events = await _stream_events(
            client,
            "POST",
            f"/threads/{thread_id}/approve",
            {"approved": False, "comment": "already ordered"},
        )

    kinds = [e["event"] for e in events]
    assert "tool_start" not in kinds
    assert kinds[-2:] == ["usage", "done"]
    assert harness.effects.send_email_calls == []


async def test_approving_a_thread_with_nothing_pending_is_409() -> None:
    app, _ = _app_for(script().final("just chatting").build())
    async with _client(app) as client:
        thread_id = (await client.post("/threads", headers=AUTH)).json()["thread_id"]
        await _stream_events(client, "POST", f"/threads/{thread_id}/messages", {"content": "hi"})
        response = await client.post(
            f"/threads/{thread_id}/approve", json={"approved": True}, headers=AUTH
        )

    assert response.status_code == 409
    assert "not waiting for approval" in response.json()["detail"]


# --- 4. unknown thread -> 404 with a fix ------------------------------------


async def test_unknown_thread_is_404_with_a_fix() -> None:
    app, _ = _app_for(_SEND_EMAIL_SCRIPT)
    async with _client(app) as client:
        state = await client.get("/threads/never-created/state", headers=AUTH)
        approve = await client.post(
            "/threads/never-created/approve", json={"approved": True}, headers=AUTH
        )

    assert state.status_code == 404
    assert "POST /threads" in state.json()["detail"]
    assert approve.status_code == 404


async def test_empty_message_content_is_rejected() -> None:
    app, _ = _app_for(_SEND_EMAIL_SCRIPT)
    async with _client(app) as client:
        response = await client.post("/threads/any/messages", json={"content": ""}, headers=AUTH)

    assert response.status_code == 422


# --- 5. audit 001 (T16): one run per thread, limits, deletion ---------------


async def test_messages_while_awaiting_approval_is_409_with_a_fix() -> None:
    """AUD-002: a new message can't fork a thread that is paused for approval."""
    app, harness = _app_for(_SEND_EMAIL_SCRIPT)
    async with _client(app) as client:
        thread_id = (await client.post("/threads", headers=AUTH)).json()["thread_id"]
        await _stream_events(
            client, "POST", f"/threads/{thread_id}/messages", {"content": "reorder please"}
        )

        response = await client.post(
            f"/threads/{thread_id}/messages", json={"content": "and one more"}, headers=AUTH
        )

    assert response.status_code == 409
    assert "/approve" in response.json()["detail"]
    assert harness.effects.send_email_calls == []


async def test_concurrent_approvals_of_one_action_run_the_effect_once() -> None:
    """AUD-001: two /approve for the same pending action — exactly one resumes
    the graph and sends. The other is told there is nothing left: 409 if it
    arrived after the first had finished, otherwise a single `error` event
    from the re-check under the thread lock."""
    app, harness = _app_for(
        script()
        .tool_call(
            "send_reorder_email", {"to": "ops@example.com", "subject": "Reorder", "body": "b"}
        )
        .final("Sent it.")
        .build()
    )
    async with _client(app) as client:
        thread_id = (await client.post("/threads", headers=AUTH)).json()["thread_id"]
        await _stream_events(
            client, "POST", f"/threads/{thread_id}/messages", {"content": "reorder please"}
        )

        results = await asyncio.gather(
            request_sse(client, "POST", f"/threads/{thread_id}/approve", {"approved": True}),
            request_sse(client, "POST", f"/threads/{thread_id}/approve", {"approved": True}),
        )

    assert len(harness.effects.send_email_calls) == 1
    winners = [r for r in results if r[0] == 200 and kinds(r[1])[-1:] == ["done"]]
    losers = [r for r in results if r not in winners]
    assert len(winners) == 1 and len(losers) == 1
    status, events = losers[0]
    assert status == 409 or kinds(events) == ["error"]
    harness.model.assert_exhausted()


async def test_concurrent_messages_on_one_thread_run_one_after_another() -> None:
    """AUD-002/003: two messages racing on one thread serialize (H, A, H, A)
    and the thread's usage is the sum of both turns, not the last one."""
    app, harness = _app_for(
        script()
        .final("first reply", input_tokens=10, output_tokens=1)
        .final("second reply", input_tokens=20, output_tokens=2)
        .build()
    )
    async with _client(app) as client:
        thread_id = (await client.post("/threads", headers=AUTH)).json()["thread_id"]

        first, second = await asyncio.gather(
            _stream_events(client, "POST", f"/threads/{thread_id}/messages", {"content": "one"}),
            _stream_events(client, "POST", f"/threads/{thread_id}/messages", {"content": "two"}),
        )
        state = (await client.get(f"/threads/{thread_id}/state", headers=AUTH)).json()
        messages = (await harness.graph.aget_state(thread_config(thread_id))).values["messages"]

    assert sorted([token_text(first), token_text(second)]) == ["first reply", "second reply"]
    assert [type(m).__name__ for m in messages] == [
        "HumanMessage",
        "AIMessage",
        "HumanMessage",
        "AIMessage",
    ]
    assert state["message_count"] == 4
    assert state["usage"] == {"input_tokens": 30, "output_tokens": 3, "calls": 2}
    harness.model.assert_exhausted()


async def test_a_message_queued_behind_a_run_that_pauses_gets_one_error_event() -> None:
    """AUD-002: two messages race on one thread. Whichever runs first pauses
    the thread for approval; the one queued behind it is refused by the
    re-check under the lock — a single `error` event — instead of being run
    on top of the interrupt (or 409 if it only arrived after the pause)."""
    app, harness = _app_for(
        script()
        .tool_call(
            "send_reorder_email", {"to": "ops@example.com", "subject": "Reorder", "body": "b"}
        )
        .build()
    )
    async with _client(app) as client:
        thread_id = (await client.post("/threads", headers=AUTH)).json()["thread_id"]

        results = await asyncio.gather(
            request_sse(client, "POST", f"/threads/{thread_id}/messages", {"content": "reorder"}),
            request_sse(client, "POST", f"/threads/{thread_id}/messages", {"content": "hello"}),
        )

    paused = [r for r in results if r[0] == 200 and kinds(r[1])[-1:] == ["interrupt"]]
    refused = [r for r in results if r not in paused]
    assert len(paused) == 1 and len(refused) == 1
    status, events = refused[0]
    assert status == 409 or kinds(events) == ["error"]
    assert harness.effects.send_email_calls == []
    harness.model.assert_exhausted()


async def test_delete_thread_removes_its_history() -> None:
    app, _ = _app_for(script().final("bye").build())
    async with _client(app) as client:
        thread_id = (await client.post("/threads", headers=AUTH)).json()["thread_id"]
        await _stream_events(client, "POST", f"/threads/{thread_id}/messages", {"content": "hi"})
        assert (await client.get(f"/threads/{thread_id}/state", headers=AUTH)).status_code == 200

        deleted = await client.delete(f"/threads/{thread_id}", headers=AUTH)
        gone = await client.get(f"/threads/{thread_id}/state", headers=AUTH)
        again = await client.delete(f"/threads/{thread_id}", headers=AUTH)

    assert deleted.status_code == 204
    assert gone.status_code == 404
    assert again.status_code == 404


async def test_delete_requires_auth_and_an_existing_thread() -> None:
    app, _ = _app_for(_SEND_EMAIL_SCRIPT)
    async with _client(app) as client:
        unauthorized = await client.delete("/threads/x")
        missing = await client.delete("/threads/never-created", headers=AUTH)

    assert unauthorized.status_code == 401
    assert missing.status_code == 404


async def test_an_oversized_body_is_refused_before_it_is_parsed() -> None:
    """AUD-005: Content-Length over the limit is 413 from the ASGI middleware."""
    app, _ = _app_for(_SEND_EMAIL_SCRIPT)
    huge = json.dumps({"content": "a" * (MAX_BODY_BYTES + 1)}).encode()
    async with _client(app) as client:
        response = await client.post(
            "/threads/x/messages",
            content=huge,
            headers={**AUTH, "content-type": "application/json"},
        )

    assert response.status_code == 413
    assert "limit" in response.json()["detail"]


async def test_over_long_message_content_is_422() -> None:
    app, _ = _app_for(_SEND_EMAIL_SCRIPT)
    async with _client(app) as client:
        response = await client.post(
            "/threads/x/messages",
            json={"content": "a" * (MAX_MESSAGE_CHARS + 1)},
            headers=AUTH,
        )

    assert response.status_code == 422


# --- the production wiring (`make dev` path) --------------------------------


async def test_create_default_app_wires_settings_and_a_sqlite_checkpointer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`create_default_app()` reads `.env`-style settings and opens the real
    model + AsyncSqliteSaver. Nothing here invokes the model, so no network
    call happens (guardrail 2) — this proves the assembly, not the model.
    """
    db_path = tmp_path / "data" / "checkpoints.db"
    monkeypatch.setenv("APP_BEARER_TOKEN", "from-env")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")  # startup check, no call
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(db_path))
    monkeypatch.setenv("SEND_MODE", "dry_run")

    app = create_default_app()
    async with _client(app) as client:
        unauthorized = await client.post("/threads")
        created = await client.post("/threads", headers={"Authorization": "Bearer from-env"})
        missing = await client.get(
            "/threads/nope/state", headers={"Authorization": "Bearer from-env"}
        )

    assert unauthorized.status_code == 401
    assert created.status_code == 200
    assert missing.status_code == 404
    assert db_path.exists()  # the lifespan really opened AsyncSqliteSaver there


async def test_create_default_app_exports_dotenv_to_the_process_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Provider clients read their API key from os.environ, and
    pydantic-settings never exports .env there — so a .env-only
    ANTHROPIC_API_KEY silently never reached the model. Found by the first
    real-model smoke; this pins the fix (load_dotenv at startup).
    """
    monkeypatch.chdir(tmp_path)
    for var in ("ANTHROPIC_API_KEY", "APP_BEARER_TOKEN", "CHECKPOINT_DB_PATH"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / ".env").write_text(
        "APP_BEARER_TOKEN=from-dotenv\nANTHROPIC_API_KEY=sk-test-not-a-real-key\n"
        "CHECKPOINT_DB_PATH=cp.db\n"
    )

    app = create_default_app()

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-test-not-a-real-key"  # reached the process env
    async with _client(app) as client:
        created = await client.post("/threads", headers={"Authorization": "Bearer from-dotenv"})
    assert created.status_code == 200


def test_create_default_app_fails_fast_when_the_provider_key_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DESIGN §8.1: a missing key is a startup ConfigError naming the env var
    and the fix — not an SSE `error` on the first message.
    """
    monkeypatch.chdir(tmp_path)  # no .env
    monkeypatch.setenv("APP_BEARER_TOKEN", "t")
    monkeypatch.setenv("MODEL", "openai:gpt-5")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ConfigError, match="OPENAI_API_KEY") as exc_info:
        create_default_app()

    assert "lang-ai-agent init" in str(exc_info.value)


def test_create_default_app_fails_fast_when_the_bearer_token_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APP_BEARER_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")

    with pytest.raises(ConfigError, match="APP_BEARER_TOKEN") as exc_info:
        create_default_app()

    assert "lang-ai-agent init" in str(exc_info.value)


async def test_create_default_app_loads_mcp_tools_when_a_path_is_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """MCP_SERVERS_PATH set -> the config is read, the loader runs, and its
    tools merge into the graph at startup. The loader itself is replaced
    (it would spawn a server process); the wiring around it is what's tested.
    """
    import lang_ai_agent.api.app as app_module
    from lang_ai_agent.adapters.mcp_loader import McpServersConfig
    from lang_ai_agent.core.tools_spec import ToolSpec

    mcp_file = tmp_path / "mcp_servers.json"
    mcp_file.write_text(json.dumps({"retail": {"command": "npx", "args": ["server"]}}))
    monkeypatch.setenv("APP_BEARER_TOKEN", "from-env")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "cp.db"))
    monkeypatch.setenv("MCP_SERVERS_PATH", str(mcp_file))

    loaded_with: list[McpServersConfig] = []

    async def fake_load(config: McpServersConfig) -> list[ToolSpec]:
        loaded_with.append(config)
        return []

    monkeypatch.setattr(app_module, "load_mcp_tool_specs", fake_load)

    app = create_default_app()
    async with _client(app) as client:
        created = await client.post("/threads", headers={"Authorization": "Bearer from-env"})

    assert created.status_code == 200
    assert len(loaded_with) == 1 and set(loaded_with[0]) == {"retail"}
