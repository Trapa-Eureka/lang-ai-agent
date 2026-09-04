"""Round-trip tests for the SSE event schema (T1 completion criteria)."""

import pytest
from pydantic import TypeAdapter, ValidationError

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
from lang_ai_agent.core.state import PendingAction, Usage

_EVENT_ADAPTER: TypeAdapter[SSEEvent] = TypeAdapter(SSEEvent)

_SAMPLE_PENDING = PendingAction(
    tool_call_id="call_2",
    tool_name="send_reorder_email",
    args_preview={"to": "ops@example.com"},
)

EVENTS = [
    TokenEvent(content="Store 12 is "),
    ToolStartEvent(tool_call_id="call_1", tool_name="check_stockout"),
    ToolEndEvent(tool_call_id="call_1", tool_name="check_stockout", duration_ms=42.5),
    InterruptEvent(pending=_SAMPLE_PENDING, draft="Reordering 3 items for Store 12."),
    UsageEvent(usage=Usage(input_tokens=100, output_tokens=20, calls=1)),
    DoneEvent(),
    ErrorEvent(message="tool check_stockout failed: timeout"),
]


@pytest.mark.parametrize("event", EVENTS, ids=[e.type for e in EVENTS])
def test_sse_event_round_trips_through_the_discriminated_union(event: SSEEvent) -> None:
    dumped = _EVENT_ADAPTER.dump_python(event, mode="json")

    restored = _EVENT_ADAPTER.validate_python(dumped)

    assert restored == event
    assert dumped["type"] == event.type


def test_discriminator_rejects_an_unknown_event_type() -> None:
    with pytest.raises(ValidationError):
        _EVENT_ADAPTER.validate_python({"type": "not_a_real_event"})


def test_done_event_carries_no_extra_fields_by_default() -> None:
    assert DoneEvent().model_dump() == {"type": "done"}
