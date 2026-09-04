"""The approval-gate edge cases (docs/TESTING.md §4, "그래프·승인 게이트").

Five items, one test (or small group) each:
1. structural invariant — effect_tools is reachable only from approval
2. interrupt payload carries a pending summary + draft, nothing large
3. the resume value (approved/comment) reaches interrupt()'s return exactly
4. a rejection comment reaches the model as a ToolMessage
5. SEND_MODE=dry_run means approval never reaches the real send path
"""

import logging

import pytest

from lang_ai_agent.adapters.effects import SendMode
from lang_ai_agent.core.graph import APPROVAL, EFFECT_TOOLS
from lang_ai_agent.core.state import PendingAction
from tests.component.conftest import GraphHarness, MakeHarness
from tests.helpers.scripted_chat_model import script

_SEND_EMAIL_SCRIPT = (
    script()
    .tool_call(
        "send_reorder_email", {"to": "ops@example.com", "subject": "Reorder", "body": "35 units"}
    )
    .final("Sent it.")
    .build()
)


# --- 1. structural invariant -------------------------------------------------


def test_effect_tools_is_reachable_only_through_approval(make_harness: MakeHarness) -> None:
    harness: GraphHarness = make_harness(_SEND_EMAIL_SCRIPT)

    edges_into_effect_tools = {
        edge.source for edge in harness.graph.get_graph().edges if edge.target == EFFECT_TOOLS
    }

    assert edges_into_effect_tools == {APPROVAL}


# --- 2. interrupt payload: summary + draft, nothing large --------------------


async def test_interrupt_payload_carries_pending_and_draft_only(make_harness: MakeHarness) -> None:
    harness: GraphHarness = make_harness(_SEND_EMAIL_SCRIPT)

    _visited, result = await harness.run("reorder please")

    payload = result["interrupt"].value
    assert set(payload.keys()) == {"action", "draft"}

    pending = payload["action"]
    assert isinstance(pending, PendingAction)
    assert pending.tool_name == "send_reorder_email"
    assert pending.args_preview == {
        "to": "ops@example.com",
        "subject": "Reorder",
        "body": "35 units",
    }
    assert payload["draft"] == "35 units"
    # nothing beyond the small args dict this test itself supplied — no
    # room for a large raw payload to have snuck in.
    assert len(str(pending.args_preview)) < 200


# --- 3. resume value reaches interrupt()'s return value exactly -------------


async def test_resume_approved_true_takes_the_effect_path_regardless_of_comment(
    make_harness: MakeHarness,
) -> None:
    harness: GraphHarness = make_harness(_SEND_EMAIL_SCRIPT)
    await harness.run("reorder please")

    visited, _result = await harness.resume(approved=True, comment="looks good")

    assert EFFECT_TOOLS in visited
    assert harness.effects.send_email_calls


async def test_resume_approved_false_never_takes_the_effect_path(make_harness: MakeHarness) -> None:
    harness: GraphHarness = make_harness(_SEND_EMAIL_SCRIPT)
    await harness.run("reorder please")

    visited, _result = await harness.resume(approved=False, comment="no")

    assert EFFECT_TOOLS not in visited
    assert harness.effects.send_email_calls == []


# --- 4. rejection comment reaches the model as a ToolMessage ----------------


async def test_rejection_comment_is_delivered_to_the_model_as_a_tool_message(
    make_harness: MakeHarness,
) -> None:
    harness: GraphHarness = make_harness(
        script()
        .tool_call(
            "send_reorder_email", {"to": "ops@example.com", "subject": "Reorder", "body": "b"}
        )
        .final("Got it, I won't send since the store already reordered.")
        .build()
    )
    await harness.run("reorder please")

    _visited, after = await harness.resume(approved=False, comment="store already reordered")

    tool_messages = [m for m in after["approval"]["messages"] if type(m).__name__ == "ToolMessage"]
    assert len(tool_messages) == 1
    assert "store already reordered" in tool_messages[0].content
    # the scripted final response only makes sense as a reaction to having
    # seen that ToolMessage — proving it actually reached the model, not
    # just that it exists somewhere in state.
    assert "already reordered" in after["agent"]["messages"][-1].content
    harness.model.assert_exhausted()


# --- 5. SEND_MODE=dry_run: approval never reaches the real send path -------


async def test_dry_run_approval_never_reaches_the_live_send_path(make_harness: MakeHarness) -> None:
    harness: GraphHarness = make_harness(_SEND_EMAIL_SCRIPT, send_mode=SendMode.DRY_RUN)
    await harness.run("reorder please")

    await harness.resume(approved=True)

    assert harness.effects.send_email_calls  # the tool ran (approved)
    assert harness.effects.live_send_calls == []  # but the double gate held


async def test_live_mode_approval_does_reach_the_send_path(make_harness: MakeHarness) -> None:
    harness: GraphHarness = make_harness(_SEND_EMAIL_SCRIPT, send_mode=SendMode.LIVE)
    await harness.run("reorder please")

    await harness.resume(approved=True)

    assert harness.effects.live_send_calls


# --- regression: both custom AgentState types must survive checkpointing ---


async def test_checkpoint_serde_does_not_block_pending_or_usage(
    make_harness: MakeHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """`PendingAction` and `Usage` (core/state.py) are plain Pydantic models,
    not LangGraph's own message/checkpoint types — adapters/checkpoint.py
    must allow-list *both* with its checkpointer's serde, or one of them
    gets silently blocked from proper deserialization (caught the hard way:
    an earlier fix allow-listed only PendingAction and left Usage blocked).
    """
    harness: GraphHarness = make_harness(_SEND_EMAIL_SCRIPT)

    with caplog.at_level(logging.WARNING, logger="langgraph.checkpoint.serde.jsonplus"):
        await harness.run("reorder please")
        await harness.resume(approved=True)

    problems = [
        record.message
        for record in caplog.records
        if "unregistered type" in record.message or "Blocked deserialization" in record.message
    ]
    assert problems == []
