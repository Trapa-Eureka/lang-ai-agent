"""The astream_events -> SSEEvent mapper (T6 completion criteria).

docs/TESTING.md's stated order: token* -> tool_start/end* -> (interrupt |
usage -> done). Every event streamed by GraphHarness.sse_run/sse_resume must
also validate against the SSEEvent schema itself (T1) — the mapper's whole
job is to stay inside that contract.
"""

from pydantic import TypeAdapter

from lang_ai_agent.api.sse import (
    DoneEvent,
    ErrorEvent,
    InterruptEvent,
    SSEEvent,
    TokenEvent,
    ToolEndEvent,
    ToolStartEvent,
    UsageEvent,
)
from lang_ai_agent.core.state import PendingAction
from tests.component.conftest import GraphHarness, MakeHarness
from tests.helpers.scripted_chat_model import script

_EVENT_ADAPTER: TypeAdapter[SSEEvent] = TypeAdapter(SSEEvent)


def _assert_all_valid_events(events: list[SSEEvent]) -> None:
    """Every event the mapper yields must itself be a valid SSEEvent — round
    tripping it through the schema is what "이벤트 스키마는 Pydantic으로 고정" means.
    """
    for event in events:
        _EVENT_ADAPTER.validate_python(_EVENT_ADAPTER.dump_python(event, mode="json"))


async def test_query_only_stream_is_tool_events_then_tokens_then_usage_done(
    make_harness: MakeHarness,
) -> None:
    harness: GraphHarness = make_harness(
        script().tool_call("check_stockout", {"store": "main"}).final("All good here").build()
    )

    events = await harness.sse_run("what's at risk?")

    _assert_all_valid_events(events)
    kinds = [event.type for event in events]
    # tool ran before the model had anything to say, so it's not literally
    # "token* first" here — but the terminal shape (usage -> done, nothing
    # after) is exactly what DESIGN promises regardless of turn count.
    assert kinds == ["tool_start", "tool_end", "token", "token", "token", "usage", "done"]
    token_text = "".join(e.content for e in events if isinstance(e, TokenEvent))
    assert token_text == "All good here"
    harness.model.assert_exhausted()


async def test_tool_start_and_tool_end_share_a_correlating_id(make_harness: MakeHarness) -> None:
    harness: GraphHarness = make_harness(
        script().tool_call("check_stockout", {"store": "main"}).final("ok").build()
    )

    events = await harness.sse_run("check")

    start = next(e for e in events if isinstance(e, ToolStartEvent))
    end = next(e for e in events if isinstance(e, ToolEndEvent))
    assert start.tool_call_id == end.tool_call_id
    assert start.tool_name == end.tool_name == "check_stockout"
    assert end.duration_ms >= 0.0


async def test_interrupt_ends_the_stream_with_pending_and_draft(
    make_harness: MakeHarness,
) -> None:
    harness: GraphHarness = make_harness(
        script()
        .tool_call(
            "send_reorder_email",
            {"to": "ops@example.com", "subject": "Reorder", "body": "35 units"},
        )
        .final("Sent it.")
        .build()
    )

    events = await harness.sse_run("reorder please")

    assert [e.type for e in events] == ["interrupt"]
    interrupt_event = events[0]
    assert isinstance(interrupt_event, InterruptEvent)
    assert isinstance(interrupt_event.pending, PendingAction)
    assert interrupt_event.pending.tool_name == "send_reorder_email"
    assert interrupt_event.draft == "35 units"
    # nothing terminal (usage/done) leaks out alongside an interrupt
    assert not any(isinstance(e, (UsageEvent, DoneEvent)) for e in events)


async def test_resume_after_interrupt_streams_effect_then_usage_done(
    make_harness: MakeHarness,
) -> None:
    harness: GraphHarness = make_harness(
        script()
        .tool_call(
            "send_reorder_email", {"to": "ops@example.com", "subject": "Reorder", "body": "b"}
        )
        .final("Sent it.")
        .build()
    )
    await harness.sse_run("reorder please")

    events = await harness.sse_resume(approved=True)

    _assert_all_valid_events(events)
    # exact token *count* is a ScriptedChatModel word-splitting detail, not
    # part of the contract — assert the shape and the reassembled text
    # separately instead of hardcoding how many "token" events there are.
    kinds = [e.type for e in events]
    assert kinds[:2] == ["tool_start", "tool_end"]
    assert kinds[-2:] == ["usage", "done"]
    assert set(kinds[2:-2]) == {"token"}
    token_text = "".join(e.content for e in events if isinstance(e, TokenEvent))
    assert token_text == "Sent it."
    harness.model.assert_exhausted()


async def test_rejected_resume_never_streams_a_tool_event(make_harness: MakeHarness) -> None:
    harness: GraphHarness = make_harness(
        script()
        .tool_call(
            "send_reorder_email", {"to": "ops@example.com", "subject": "Reorder", "body": "b"}
        )
        .final("Understood.")
        .build()
    )
    await harness.sse_run("reorder please")

    events = await harness.sse_resume(approved=False, comment="no")

    kinds = [e.type for e in events]
    assert "tool_start" not in kinds
    assert "tool_end" not in kinds
    assert kinds == ["token", "usage", "done"]


async def test_a_model_error_becomes_a_single_error_event(make_harness: MakeHarness) -> None:
    """An empty script means the very first model call already exhausts it —
    ScriptExhaustedError must surface as one ErrorEvent, not a crash.
    """
    harness: GraphHarness = make_harness([])

    events = await harness.sse_run("hi")

    assert len(events) == 1
    assert isinstance(events[0], ErrorEvent)
    assert "exhausted" in events[0].message
