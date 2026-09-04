"""Tests for ScriptedChatModel and its script() builder (T2 completion criteria)."""

import pytest

from tests.helpers.scripted_chat_model import (
    ScriptedChatModel,
    ScriptedToolCall,
    ScriptExhaustedError,
    script,
)

# --- exhaustion: calling the model more times than the script provides -----


def test_calling_past_the_script_raises_a_clear_exhaustion_error() -> None:
    model = ScriptedChatModel(script=script().final("only turn").build())

    model.invoke("first call consumes the only scripted turn")

    with pytest.raises(ScriptExhaustedError, match="exhausted after 1 call"):
        model.invoke("second call has nothing left to replay")


async def test_calling_past_the_script_raises_the_same_error_async() -> None:
    model = ScriptedChatModel(script=script().final("only turn").build())

    await model.ainvoke("consumes the only scripted turn")

    with pytest.raises(ScriptExhaustedError):
        await model.ainvoke("nothing left")


# --- leftover: script has turns nobody consumed -----------------------------


def test_assert_exhausted_fails_when_turns_are_left_unconsumed() -> None:
    model = ScriptedChatModel(script=script().final("a").final("b").build())

    model.invoke("only consumes the first of two turns")

    with pytest.raises(AssertionError, match=r"1 unconsumed.*1/2 consumed"):
        model.assert_exhausted()


def test_assert_exhausted_passes_once_every_turn_is_consumed() -> None:
    model = ScriptedChatModel(script=script().final("a").final("b").build())

    model.invoke("turn 1")
    model.invoke("turn 2")

    model.assert_exhausted()  # must not raise


def test_assert_exhausted_passes_trivially_for_an_empty_script() -> None:
    ScriptedChatModel(script=[]).assert_exhausted()  # must not raise


# --- tool_calls replay --------------------------------------------------


def test_replays_a_tool_call_then_a_final_text_turn() -> None:
    model = ScriptedChatModel(
        script=script()
        .tool_call("check_stockout", {"store": "main"})
        .final("Store main has no stockout risk.")
        .build()
    )

    first = model.invoke("what's at risk?")
    second = model.invoke("(tool result fed back in)")

    assert first.tool_calls == [
        {"name": "check_stockout", "args": {"store": "main"}, "id": "call_1", "type": "tool_call"}
    ]
    assert first.content == ""
    assert second.tool_calls == []
    assert second.content == "Store main has no stockout risk."


def test_replays_multiple_tool_calls_within_a_single_turn() -> None:
    """The "safe + effect in one turn" shape from docs/TESTING.md §3."""
    model = ScriptedChatModel(
        script=script()
        .tool_calls(
            [
                ScriptedToolCall("get_reorder_suggestions", {}),
                ScriptedToolCall("send_reorder_email", {"to": "ops@example.com"}),
            ]
        )
        .build()
    )

    turn = model.invoke("reorder the at-risk items")

    assert [c["name"] for c in turn.tool_calls] == [
        "get_reorder_suggestions",
        "send_reorder_email",
    ]
    # auto-generated ids are unique so downstream ToolMessages can correlate
    assert len({c["id"] for c in turn.tool_calls}) == 2


def test_tool_call_id_can_be_pinned_explicitly() -> None:
    model = ScriptedChatModel(
        script=script()
        .tool_call("send_reorder_email", {"to": "ops@example.com"}, tool_call_id="fixed-id")
        .build()
    )

    turn = model.invoke("reorder")

    assert turn.tool_calls[0]["id"] == "fixed-id"


# --- graph-shaped usage: bind_tools() must not break replay -----------------


def test_bind_tools_does_not_change_what_gets_replayed() -> None:
    model = ScriptedChatModel(script=script().final("ok").build())

    bound = model.bind_tools([])
    result = bound.invoke("hi")

    assert result.content == "ok"
