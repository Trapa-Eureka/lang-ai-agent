# pyright: reportUnknownMemberType=false
# CompiledStateGraph.ainvoke is overloaded heavily enough that pyright can't
# fully resolve it even for textbook-correct usage (same finding as
# core/graph.py). Scoped to this file.
"""Structured-log snapshot (T8 completion criterion): every node run and tool
call logs one record carrying thread_id, node/tool and duration_ms, and
rendering those records through the JSON formatter gives exactly the
expected lines. FixedClock makes the durations exact rather than fuzzy.
"""

import json
import logging

import pytest
from langchain_core.messages import HumanMessage

from lang_ai_agent.adapters.builtin_tools import build_builtin_tool_specs
from lang_ai_agent.adapters.checkpoint import build_memory_checkpointer, thread_config
from lang_ai_agent.adapters.observability import JsonFormatter
from lang_ai_agent.core.graph import build_graph
from lang_ai_agent.core.state import AgentState, Usage
from tests.helpers.fixed_clock import FixedClock
from tests.helpers.mock_effects import MockEffects
from tests.helpers.scripted_chat_model import ScriptedChatModel, script


async def test_every_node_and_tool_logs_thread_node_tool_and_duration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    model = ScriptedChatModel(
        script=script().tool_call("check_stockout", {"store": "main"}).final("done").build()
    )
    # step=0.25s: any span that reads the clock twice measures exactly 250ms.
    graph = build_graph(
        model,
        build_builtin_tool_specs(MockEffects()),
        checkpointer=build_memory_checkpointer(),
        clock=FixedClock(step=0.25),
    )
    initial: AgentState = {
        "messages": [HumanMessage(content="hi")],
        "pending": None,
        "usage": Usage(),
    }

    with caplog.at_level(logging.INFO, logger="lang_ai_agent.graph"):
        await graph.ainvoke(initial, thread_config("obs-thread"))

    lines = [
        json.loads(JsonFormatter().format(r))
        for r in caplog.records
        if r.name == "lang_ai_agent.graph"
    ]

    # agent -> safe_tools (which times its one tool call inside) -> agent
    snapshot = [
        (
            line["message"],
            line.get("node") or line.get("tool"),
            line["duration_ms"],
            line["thread_id"],
        )
        for line in lines
    ]
    assert snapshot == [
        ("node", "agent", 250.0, "obs-thread"),
        ("tool", "check_stockout", 250.0, "obs-thread"),
        ("node", "safe_tools", 750.0, "obs-thread"),  # its own 2 reads + the tool's 2 in between
        ("node", "agent", 250.0, "obs-thread"),
    ]
    tool_line = next(line for line in lines if line["message"] == "tool")
    assert tool_line["tool_call_id"] == "call_1"
    assert tool_line["ok"] is True
    assert all(
        line["level"] == "INFO" and line["logger"] == "lang_ai_agent.graph" for line in lines
    )


async def test_a_failing_tool_is_logged_with_ok_false(caplog: pytest.LogCaptureFixture) -> None:
    from lang_ai_agent.adapters.builtin_tools import make_check_stockout
    from lang_ai_agent.core.tools_spec import ToolSpec

    failing = ToolSpec(tool=make_check_stockout(fail_on=lambda _a: True), requires_approval=False)
    model = ScriptedChatModel(
        script=script().tool_call("check_stockout", {"store": "main"}).final("sorry").build()
    )
    graph = build_graph(model, [failing], checkpointer=build_memory_checkpointer())
    initial: AgentState = {
        "messages": [HumanMessage(content="hi")],
        "pending": None,
        "usage": Usage(),
    }

    with caplog.at_level(logging.INFO, logger="lang_ai_agent.graph"):
        await graph.ainvoke(initial, thread_config("obs-fail"))

    tool_records = [r for r in caplog.records if r.getMessage() == "tool"]
    assert len(tool_records) == 1
    # `extra=` fields are dynamic attributes on the record, not declared ones
    assert tool_records[0].__dict__["ok"] is False
    assert tool_records[0].__dict__["thread_id"] == "obs-fail"
