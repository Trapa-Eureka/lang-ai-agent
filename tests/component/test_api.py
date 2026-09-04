# pyright: reportUnknownMemberType=false
# httpx's streaming helpers and Starlette's lifespan_context are typed
# loosely enough to trip this check on ordinary usage. Scoped to this file.
"""The FastAPI service (T7 completion criteria — docs/TESTING.md §4 "API·SSE",
driven through httpx's ASGI transport, no server process).

1. no / wrong auth -> 401
2. the messages stream's event order: token* -> tool_start/end* -> (interrupt | usage -> done)
3. after an interrupt, GET state exposes pending; approve then streams through to done
4. an unknown thread_id -> 404 with a fix in the message
"""

import json
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.messages import AIMessage

from lang_ai_agent.api.app import create_app, create_default_app
from lang_ai_agent.api.auth import require_bearer_token
from tests.component.conftest import GraphHarness
from tests.helpers.scripted_chat_model import script

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

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


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient, None]:
    # httpx's ASGITransport doesn't run lifespan on its own — enter it
    # explicitly so app.state.graph exists, exactly as uvicorn would.
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def _stream_events(
    client: httpx.AsyncClient, method: str, url: str, body: dict[str, Any]
) -> list[dict[str, Any]]:
    """Consume an SSE response into [{"event": ..., "data": {...}}, ...]."""
    events: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    async with client.stream(method, url, json=body, headers=AUTH) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                current["event"] = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                current["data"] = json.loads(line.removeprefix("data:").strip())
            elif not line and current:
                events.append(current)
                current = {}
    return events


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
