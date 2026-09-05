"""Round-trip and shape tests for core/state.py (T1 completion criteria)."""

from langchain_core.messages import AIMessage, HumanMessage
from pydantic import TypeAdapter

from lang_ai_agent.core.state import AgentState, PendingAction, Usage, add_usage


def test_pending_action_round_trips_through_json() -> None:
    original = PendingAction(
        tool_call_id="call_1",
        tool_name="send_reorder_email",
        args_preview={"to": "ops@example.com", "subject": "Reorder: Store 12"},
    )

    restored = PendingAction.model_validate_json(original.model_dump_json())

    assert restored == original


def test_usage_round_trips_through_json() -> None:
    original = Usage(input_tokens=123, output_tokens=45, calls=2)

    restored = Usage.model_validate_json(original.model_dump_json())

    assert restored == original


def test_usage_defaults_to_zero() -> None:
    assert Usage() == Usage(input_tokens=0, output_tokens=0, calls=0)


def test_add_usage_sums_every_field() -> None:
    total = add_usage(
        Usage(input_tokens=10, output_tokens=2, calls=1),
        Usage(input_tokens=5, output_tokens=1, calls=1),
    )

    assert total == Usage(input_tokens=15, output_tokens=3, calls=2)


def test_add_usage_with_an_empty_update_keeps_the_total() -> None:
    """The `Usage()` every /messages request submits as input must add
    nothing — the reducer is what stops it resetting the thread's total
    (audit 001, AUD-003)."""
    total = Usage(input_tokens=10, output_tokens=2, calls=1)

    assert add_usage(total, Usage()) == total


def test_agent_state_shape_validates_with_messages_and_pending() -> None:
    """AgentState is a TypedDict (no runtime validation of its own), but
    pydantic's TypeAdapter can still check the shape — this doubles as the
    "serialization round trip" for the state schema as a whole.
    """
    pending = PendingAction(tool_call_id="call_1", tool_name="send_reorder_email", args_preview={})
    state: AgentState = {
        "messages": [HumanMessage(content="reorder the at-risk items"), AIMessage(content="")],
        "pending": pending,
        "usage": Usage(calls=1),
    }

    validated = TypeAdapter(AgentState).validate_python(state)

    assert validated["pending"] == pending
    assert validated["usage"] == Usage(calls=1)
    assert len(validated["messages"]) == 2


def test_agent_state_pending_is_optional() -> None:
    state: AgentState = {"messages": [], "pending": None, "usage": Usage()}

    validated = TypeAdapter(AgentState).validate_python(state)

    assert validated["pending"] is None
