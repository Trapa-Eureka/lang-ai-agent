"""Golden trajectories — hardcoded node-visit order per scenario (docs/TESTING.md §3).

Each test pins down the exact sequence of node names the graph visits, so a
change to the routing logic that alters *how* a scenario reaches its answer
fails here even if the final answer still looks right.
"""

from lang_ai_agent.adapters.effects import SendMode
from tests.component.conftest import GraphHarness, MakeHarness
from tests.helpers.scripted_chat_model import ScriptedToolCall, script


async def test_query_only_has_no_interrupt(make_harness: MakeHarness) -> None:
    """SPEC §4 scenario 1 / TESTING §3 "Query only": agent -> safe_tools -> agent -> END."""
    harness: GraphHarness = make_harness(
        script().tool_call("check_stockout", {"store": "main"}).final("2 items at risk.").build()
    )

    visited, result = await harness.run("what's at risk?")

    assert visited == ["agent", "safe_tools", "agent"]
    assert "interrupt" not in result
    assert result["agent"]["messages"][-1].content == "2 items at risk."
    harness.model.assert_exhausted()


async def test_approved_send_sequential_turns(make_harness: MakeHarness) -> None:
    """SPEC §4 scenario 2 / TESTING §3 "Approved send": a safe lookup, then (having
    seen its result) a *separate* agent turn decides to call the effect tool.
    agent -> safe_tools -> agent -> approval(interrupt) -> [resume approved]
    -> effect_tools -> agent -> END.
    """
    harness: GraphHarness = make_harness(
        script()
        .tool_call("get_reorder_suggestions", {"store": "main"})
        .tool_call(
            "send_reorder_email",
            {"to": "ops@example.com", "subject": "Reorder", "body": "35 units"},
        )
        .final("Sent the reorder email.")
        .build()
    )

    visited_before, before = await harness.run("reorder the at-risk items")
    assert visited_before == ["agent", "safe_tools", "agent"]
    assert "interrupt" in before

    visited_after, after = await harness.resume(approved=True)
    assert visited_after == ["approval", "effect_tools", "agent"]
    assert after["agent"]["messages"][-1].content == "Sent the reorder email."
    assert harness.effects.send_email_calls  # the tool actually ran
    harness.model.assert_exhausted()


async def test_rejected_send_never_executes_the_effect(make_harness: MakeHarness) -> None:
    """TESTING §3 "Rejection": approval(interrupt) -> [resume rejected+comment] ->
    agent -> END, with effect_tools never visited and the tool never run.
    """
    harness: GraphHarness = make_harness(
        script()
        .tool_call(
            "send_reorder_email", {"to": "ops@example.com", "subject": "Reorder", "body": "draft"}
        )
        .final("Understood, I won't send it.")
        .build()
    )

    visited_before, before = await harness.run("reorder please")
    assert visited_before == ["agent"]
    assert "interrupt" in before

    visited_after, after = await harness.resume(approved=False, comment="already ordered")
    assert visited_after == ["approval", "agent"]
    assert "effect_tools" not in visited_after
    assert after["agent"]["messages"][-1].content == "Understood, I won't send it."
    assert harness.effects.send_email_calls == []  # never even attempted
    harness.model.assert_exhausted()


async def test_mixed_safe_and_effect_in_one_turn(make_harness: MakeHarness) -> None:
    """TESTING §3 "Mixed tool_calls (safe + effect in one response)": both calls come from the
    *same* AIMessage. safe_tools must run before approval, with no extra
    agent turn in between (agent -> safe_tools -> approval(interrupt) ->
    [resume approved] -> effect_tools -> agent).
    """
    harness: GraphHarness = make_harness(
        script()
        .tool_calls(
            [
                ScriptedToolCall("check_stockout", {"store": "main"}),
                ScriptedToolCall(
                    "send_reorder_email",
                    {"to": "ops@example.com", "subject": "Reorder", "body": "draft"},
                ),
            ]
        )
        .final("Done.")
        .build(),
        send_mode=SendMode.LIVE,
    )

    visited_before, before = await harness.run("check and reorder")
    assert visited_before == ["agent", "safe_tools"]  # no "agent" between safe_tools and approval
    assert "interrupt" in before

    # the safe call already ran and produced its ToolMessage before the
    # interrupt — proving "all safe tools run first, then approval is entered" (DESIGN §3).
    state = await harness.state_values()
    tool_message_names = [m.name for m in state["messages"] if type(m).__name__ == "ToolMessage"]
    assert tool_message_names == ["check_stockout"]

    visited_after, after = await harness.resume(approved=True)
    assert visited_after == ["approval", "effect_tools", "agent"]
    assert after["agent"]["messages"][-1].content == "Done."
    assert harness.effects.live_send_calls  # SEND_MODE=live actually sent it
    harness.model.assert_exhausted()
