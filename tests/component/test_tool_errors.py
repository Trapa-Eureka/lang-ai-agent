# pyright: reportUnknownMemberType=false
# CompiledStateGraph.ainvoke is overloaded heavily enough that pyright can't
# fully resolve it even for textbook-correct, fully-concrete usage (same
# finding as core/graph.py and tests/unit/test_checkpoint.py). Scoped to
# this file; every other file keeps the check.
"""Tool-error handling (docs/TESTING.md §4, "도구·에러") — built alongside the
approval gate since safe_tools/effect_tools' error handling is inseparable
from building those nodes correctly, even though T5's own completion
criteria only require the approval-gate items.
"""

import pytest
from langchain_core.messages import HumanMessage

from lang_ai_agent.adapters.builtin_tools import build_builtin_tool_specs, make_check_stockout
from lang_ai_agent.adapters.checkpoint import build_memory_checkpointer, thread_config
from lang_ai_agent.core.graph import InvalidToolCallError, build_graph
from lang_ai_agent.core.state import AgentState, Usage
from lang_ai_agent.core.tools_spec import ToolSpec
from tests.helpers.mock_effects import MockEffects
from tests.helpers.scripted_chat_model import ScriptedChatModel, script


def _initial_state(content: str) -> AgentState:
    return {"messages": [HumanMessage(content=content)], "pending": None, "usage": Usage()}


async def test_unregistered_tool_call_raises_a_clear_error() -> None:
    model = ScriptedChatModel(script=script().tool_call("does_not_exist", {}).build())
    specs = build_builtin_tool_specs(MockEffects())
    graph = build_graph(model, specs, checkpointer=build_memory_checkpointer())

    with pytest.raises(InvalidToolCallError, match="does_not_exist"):
        await graph.ainvoke(_initial_state("hi"), thread_config("bad-tool"))


async def test_a_failing_safe_tool_becomes_an_error_tool_message_not_a_crash() -> None:
    """The graph must not die — the model gets a chance to react, per its script."""
    failing_check_stockout = ToolSpec(
        tool=make_check_stockout(fail_on=lambda _args: True), requires_approval=False
    )
    model = ScriptedChatModel(
        script=script()
        .tool_call("check_stockout", {"store": "main"})
        .final("Sorry, I couldn't check stock right now.")
        .build()
    )
    graph = build_graph(model, [failing_check_stockout], checkpointer=build_memory_checkpointer())

    result = await graph.ainvoke(_initial_state("check please"), thread_config("failing-tool"))

    tool_messages = [m for m in result["messages"] if type(m).__name__ == "ToolMessage"]
    assert len(tool_messages) == 1
    # the model sees type + first line only (audit 001, AUD-007), never a traceback
    assert tool_messages[0].content == (
        "Tool 'check_stockout' failed: RuntimeError: check_stockout failed for store='main' "
        "(injected failure)"
    )
    assert result["messages"][-1].content == "Sorry, I couldn't check stock right now."
    model.assert_exhausted()
