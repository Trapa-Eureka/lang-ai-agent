"""SPEC §4 scenarios 1-4, end to end through the HTTP API (T10).

Each scenario drives the real FastAPI app over httpx/ASGI exactly as a
client would — POST /threads, stream /messages, GET /state, stream
/approve — with only the two things docs/TESTING.md §1 allows replaced:
the model (ScriptedChatModel) and the effect backend (MockEffects). The
component suites already verify the graph, the SSE mapper and the API in
isolation; this file is the one place a whole scenario runs as one story.

Scenarios 1-3 use InMemorySaver; scenario 4 (restart resilience) uses a
real AsyncSqliteSaver on a temp file, because the whole point is that the
state outlives the process that wrote it.
"""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from langchain_core.messages import AIMessage

from lang_ai_agent.adapters.builtin_tools import build_builtin_tool_specs
from lang_ai_agent.adapters.checkpoint import build_sqlite_checkpointer
from lang_ai_agent.adapters.effects import SendMode
from lang_ai_agent.api.app import create_app
from lang_ai_agent.core.graph import build_graph
from tests.component.conftest import GraphHarness
from tests.helpers.http_client import (
    AUTH_HEADERS,
    TEST_TOKEN,
    api_client,
    kinds,
    read_sse,
    token_text,
)
from tests.helpers.mock_effects import MockEffects
from tests.helpers.scripted_chat_model import ScriptedChatModel, script

_REORDER_EMAIL = {
    "to": "supplier@roastworks.example",
    "subject": "Reorder: SKU-100 Espresso Beans 1kg x35",
    "body": "Please send 35 units of SKU-100 to the main store by Friday.",
}


def _app_for(
    script_messages: Sequence[AIMessage], send_mode: SendMode = SendMode.DRY_RUN
) -> tuple[FastAPI, GraphHarness]:
    harness = GraphHarness(script_messages, send_mode=send_mode)

    @asynccontextmanager
    async def graph_factory() -> AsyncGenerator[Any, None]:
        yield harness.graph

    return create_app(graph_factory, bearer_token=TEST_TOKEN), harness


def _sqlite_app_for(model: ScriptedChatModel, effects: MockEffects, db_path: Path) -> FastAPI:
    """An app whose graph lives on a real sqlite checkpointer at `db_path`.

    Each call builds a brand-new app + graph + checkpointer handle — a
    "server process" — while `model` (the external LLM provider, which does
    not restart when our server does) and `effects` are shared.
    """

    @asynccontextmanager
    async def graph_factory() -> AsyncGenerator[Any, None]:
        async with build_sqlite_checkpointer(str(db_path)) as checkpointer:
            yield build_graph(model, build_builtin_tool_specs(effects), checkpointer=checkpointer)

    return create_app(graph_factory, bearer_token=TEST_TOKEN)


async def _new_thread(client: Any) -> str:
    response = await client.post("/threads", headers=AUTH_HEADERS)
    assert response.status_code == 200
    return response.json()["thread_id"]


async def _state(client: Any, thread_id: str) -> dict[str, Any]:
    response = await client.get(f"/threads/{thread_id}/state", headers=AUTH_HEADERS)
    assert response.status_code == 200
    return response.json()


# --- scenario 1: a query needs no approval ---------------------------------


async def test_scenario_1_query_streams_a_summary_with_no_interrupt() -> None:
    summary = "본점 위험 품목 2건: SKU-100 에스프레소 원두(3일), SKU-142 귀리우유(5일)."
    app, harness = _app_for(
        script()
        .tool_call(
            "check_stockout", {"store": "main", "days_ahead": 7}, input_tokens=120, output_tokens=15
        )
        .final(summary, input_tokens=260, output_tokens=40)
        .build()
    )

    async with api_client(app) as client:
        thread_id = await _new_thread(client)
        events = await read_sse(
            client,
            "POST",
            f"/threads/{thread_id}/messages",
            {"content": "본점에서 다음 주에 떨어질 품목?"},
        )
        state = await _state(client, thread_id)

    assert "interrupt" not in kinds(events)
    assert kinds(events)[:2] == ["tool_start", "tool_end"]
    assert events[0]["data"]["tool_name"] == "check_stockout"
    assert token_text(events) == summary
    assert kinds(events)[-2:] == ["usage", "done"]
    assert state["awaiting_approval"] is False
    assert state["pending"] is None
    assert state["last_message"] == summary
    assert state["message_count"] == 4  # human, tool-calling ai, tool result, final ai
    assert state["usage"] == {"input_tokens": 380, "output_tokens": 55, "calls": 2}
    harness.model.assert_exhausted()


# --- scenario 2: an effect needs approval, then runs -----------------------


async def test_scenario_2_reorder_email_interrupts_then_sends_on_approval() -> None:
    app, harness = _app_for(
        script()
        .tool_call("get_reorder_suggestions", {"store": "main"})
        .tool_call("send_reorder_email", _REORDER_EMAIL)
        .final("공급사에 재주문 메일을 보냈습니다. 35개, 금요일 납품 요청.")
        .build()
    )

    async with api_client(app) as client:
        thread_id = await _new_thread(client)

        first = await read_sse(
            client,
            "POST",
            f"/threads/{thread_id}/messages",
            {"content": "위험 품목 재주문 메일 보내줘"},
        )
        # the safe lookup ran, then the graph paused showing draft + recipient
        assert kinds(first)[:2] == ["tool_start", "tool_end"]
        assert first[0]["data"]["tool_name"] == "get_reorder_suggestions"
        assert kinds(first)[-1] == "interrupt"
        interrupt = first[-1]["data"]
        assert interrupt["pending"]["tool_name"] == "send_reorder_email"
        assert interrupt["pending"]["args_preview"]["to"] == _REORDER_EMAIL["to"]
        assert interrupt["draft"] == _REORDER_EMAIL["body"]
        assert harness.effects.send_email_calls == []  # nothing sent before approval

        paused = await _state(client, thread_id)
        assert paused["awaiting_approval"] is True
        assert paused["pending"]["tool_name"] == "send_reorder_email"

        second = await read_sse(client, "POST", f"/threads/{thread_id}/approve", {"approved": True})
        assert kinds(second)[:2] == ["tool_start", "tool_end"]
        assert second[0]["data"]["tool_name"] == "send_reorder_email"
        assert "재주문 메일을 보냈습니다" in token_text(second)
        assert kinds(second)[-2:] == ["usage", "done"]

        final = await _state(client, thread_id)

    assert final["awaiting_approval"] is False
    assert final["pending"] is None
    assert harness.effects.send_email_calls == [_REORDER_EMAIL]
    assert harness.effects.live_send_calls == []  # SEND_MODE=dry_run: gate two held
    harness.model.assert_exhausted()


# --- scenario 3: rejection, in both shapes SPEC allows ---------------------


async def test_scenario_3a_rejection_ends_politely_without_sending() -> None:
    app, harness = _app_for(
        script()
        .tool_call("send_reorder_email", _REORDER_EMAIL)
        .final("알겠습니다. 이미 발주하셨다니 메일은 보내지 않겠습니다.")
        .build()
    )

    async with api_client(app) as client:
        thread_id = await _new_thread(client)
        await read_sse(
            client, "POST", f"/threads/{thread_id}/messages", {"content": "재주문 메일 보내줘"}
        )

        events = await read_sse(
            client,
            "POST",
            f"/threads/{thread_id}/approve",
            {"approved": False, "comment": "이미 어제 발주했어요"},
        )
        state = await _state(client, thread_id)

    assert "tool_start" not in kinds(events)  # the effect tool never ran
    assert "보내지 않겠습니다" in token_text(events)
    assert kinds(events)[-2:] == ["usage", "done"]
    assert state["awaiting_approval"] is False
    assert harness.effects.send_email_calls == []
    harness.model.assert_exhausted()


async def test_scenario_3b_rejection_comment_leads_to_a_revised_draft_then_send() -> None:
    revised = {
        **_REORDER_EMAIL,
        "body": "Please send 20 units of SKU-100 to the main store by Friday.",
    }
    app, harness = _app_for(
        script()
        .tool_call("send_reorder_email", _REORDER_EMAIL)  # draft v1
        .tool_call("send_reorder_email", revised)  # after "too many", draft v2
        .final("수량을 20개로 줄여 다시 보냈습니다.")
        .build()
    )

    async with api_client(app) as client:
        thread_id = await _new_thread(client)
        v1 = await read_sse(
            client, "POST", f"/threads/{thread_id}/messages", {"content": "재주문 메일 보내줘"}
        )
        assert v1[-1]["data"]["draft"] == _REORDER_EMAIL["body"]

        # rejecting with a comment gives the model the reason; it comes back
        # with a revised draft — a second interrupt, nothing sent yet
        v2 = await read_sse(
            client,
            "POST",
            f"/threads/{thread_id}/approve",
            {"approved": False, "comment": "35개는 너무 많아요, 20개로"},
        )
        assert kinds(v2) == ["interrupt"]
        assert v2[-1]["data"]["draft"] == revised["body"]
        assert harness.effects.send_email_calls == []
        assert (await _state(client, thread_id))["awaiting_approval"] is True

        sent = await read_sse(client, "POST", f"/threads/{thread_id}/approve", {"approved": True})

    assert kinds(sent)[:2] == ["tool_start", "tool_end"]
    assert "20개로 줄여" in token_text(sent)
    assert harness.effects.send_email_calls == [revised]  # v2 only — v1 was never sent
    harness.model.assert_exhausted()


# --- scenario 4: restart resilience ---------------------------------------


async def test_scenario_4_approval_survives_a_server_restart(tmp_path: Path) -> None:
    """Interrupt on one app, tear it down completely (graph, checkpointer
    handle, lifespan), build a fresh app on the same sqlite file, and
    approve on the same thread_id — it must resume and send.
    """
    db_path = tmp_path / "data" / "checkpoints.db"
    model = ScriptedChatModel(
        script=script()
        .tool_call("send_reorder_email", _REORDER_EMAIL)
        .final("재시작 후에도 이어서 보냈습니다.")
        .build()
    )
    effects = MockEffects(send_mode=SendMode.DRY_RUN)

    # process 1: get as far as the approval interrupt, then die
    async with api_client(_sqlite_app_for(model, effects, db_path)) as client:
        thread_id = await _new_thread(client)
        first = await read_sse(
            client, "POST", f"/threads/{thread_id}/messages", {"content": "재주문 메일 보내줘"}
        )
        assert kinds(first)[-1] == "interrupt"
    assert effects.send_email_calls == []

    # process 2: brand-new app + graph + checkpointer on the same file
    async with api_client(_sqlite_app_for(model, effects, db_path)) as client:
        restored = await _state(client, thread_id)
        assert restored["awaiting_approval"] is True  # the interrupt came back from disk
        assert restored["pending"]["tool_name"] == "send_reorder_email"

        events = await read_sse(client, "POST", f"/threads/{thread_id}/approve", {"approved": True})
        after = await _state(client, thread_id)

    assert kinds(events)[:2] == ["tool_start", "tool_end"]
    assert "재시작 후에도" in token_text(events)
    assert kinds(events)[-2:] == ["usage", "done"]
    assert after["awaiting_approval"] is False
    assert effects.send_email_calls == [_REORDER_EMAIL]
    model.assert_exhausted()


async def test_threads_are_isolated_from_each_other() -> None:
    """TESTING §4 "스레드 격리": one thread paused for approval must not leak
    into, or be disturbed by, another thread completing a plain query.
    """
    app, harness = _app_for(
        script()
        .tool_call(
            "send_reorder_email", _REORDER_EMAIL, input_tokens=10, output_tokens=1
        )  # thread A
        .final("B: 재고 이상 없음.", input_tokens=20, output_tokens=2)  # thread B
        .final("A: 보냈습니다.", input_tokens=30, output_tokens=3)  # thread A after approval
        .build()
    )

    async with api_client(app) as client:
        thread_a = await _new_thread(client)
        thread_b = await _new_thread(client)

        a_first = await read_sse(
            client, "POST", f"/threads/{thread_a}/messages", {"content": "재주문 메일"}
        )
        assert kinds(a_first)[-1] == "interrupt"

        b_events = await read_sse(
            client, "POST", f"/threads/{thread_b}/messages", {"content": "재고 괜찮아?"}
        )
        # exact token count is a word-splitting detail of the test double —
        # assert the shape and the reassembled text, not how many chunks
        assert "interrupt" not in kinds(b_events) and "tool_start" not in kinds(b_events)
        assert kinds(b_events)[-2:] == ["usage", "done"]
        assert token_text(b_events) == "B: 재고 이상 없음."

        state_a, state_b = await _state(client, thread_a), await _state(client, thread_b)
        assert state_a["awaiting_approval"] is True and state_b["awaiting_approval"] is False
        assert state_a["usage"]["calls"] == 1 and state_b["usage"]["calls"] == 1
        assert state_b["last_message"] == "B: 재고 이상 없음."

        a_done = await read_sse(client, "POST", f"/threads/{thread_a}/approve", {"approved": True})
        assert kinds(a_done)[-2:] == ["usage", "done"]
        assert (await _state(client, thread_a))["usage"] == {
            "input_tokens": 40,
            "output_tokens": 4,
            "calls": 2,
        }
        assert (await _state(client, thread_b))["usage"]["calls"] == 1  # untouched by A's approval

    assert harness.effects.send_email_calls == [_REORDER_EMAIL]
    harness.model.assert_exhausted()
