# pyright: reportUnknownMemberType=false
# StateGraph.add_node/.add_conditional_edges/.compile and
# CompiledStateGraph.ainvoke are overloaded heavily enough that pyright
# can't fully resolve them even for textbook-correct, fully-concrete usage
# (confirmed with a minimal reproduction — see tests/unit/test_checkpoint.py's
# identical note). Scoped to this file; every other file keeps the check.
"""The agent graph (docs/DESIGN.md §3) — a custom StateGraph, not
`langgraph.prebuilt` (CLAUDE.md stack: intentional).

Topology (docs/DESIGN.md §1 has the original diagram; this is the same
shape spelled out with the routing functions below named explicitly):

- agent -> route_after_agent
  - no tool_calls -> END
  - safe tool_calls present -> safe_tools -> route_after_safe_tools
    - effect tool_calls remain -> approval
    - nothing left -> agent
  - only effect tool_calls -> approval
- approval --interrupt()--> [human] --Command(resume)--> route_after_approval
  - approved -> effect_tools -> agent
  - rejected -> agent (directly, effect_tools never runs)

The one invariant this whole repo exists to demonstrate (CLAUDE.md guardrail
1): `effect_tools` is reachable *only* from `approval`. See
tests/component/test_approval_gate.py for the structural proof via
`get_graph()`.

Observability (DESIGN §7): every node run and every tool call logs one
structured record on the `lang_ai_agent.graph` logger with `thread_id`,
`node`/`tool` and `duration_ms` in `extra=`; the agent node emits each
model response's `usage_metadata` as a one-call delta that the state's
`add_usage` reducer folds into `state["usage"]`.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_core.messages.tool import ToolCall
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from pydantic import JsonValue

from lang_ai_agent.core.errors import describe_error
from lang_ai_agent.core.state import AgentState, PendingAction, Usage
from lang_ai_agent.core.tools_spec import ToolSpec

AGENT = "agent"
SAFE_TOOLS = "safe_tools"
APPROVAL = "approval"
EFFECT_TOOLS = "effect_tools"

logger = logging.getLogger("lang_ai_agent.graph")

type Clock = Callable[[], float]
"""A monotonic-seconds source. `time.monotonic` in production; tests inject
`tests/helpers/fixed_clock.py` for exact `duration_ms` assertions."""

type _Node = Callable[[AgentState, RunnableConfig], Awaitable[dict[str, Any]]]


class InvalidToolCallError(RuntimeError):
    """A tool_call this graph can't safely execute: an unregistered tool
    name, or a call missing the id needed to correlate its ToolMessage.
    """


def usage_of_call(response: AIMessage) -> Usage:
    """One model response as a usage *delta* (DESIGN §7).

    The state's `add_usage` reducer sums these into the thread total, so
    the node never reads — or accidentally resets — the running total
    itself. Reads the response's `usage_metadata` (LangChain's
    provider-neutral token counts — what a model callback would see) rather
    than hanging a callback off the model, so this stays a pure function of
    the response. A response without usage_metadata still counts as a call.
    """
    metadata = response.usage_metadata
    return Usage(
        input_tokens=metadata["input_tokens"] if metadata else 0,
        output_tokens=metadata["output_tokens"] if metadata else 0,
        calls=1,
    )


def _thread_id(config: RunnableConfig) -> str:
    return str(config.get("configurable", {}).get("thread_id", "-"))


def _find_last_ai_message_index(messages: Sequence[AnyMessage]) -> int | None:
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], AIMessage):
            return i
    return None  # pragma: no cover - defensive: routing always calls this after `agent` has run


def _answered_tool_call_ids(messages: Sequence[AnyMessage], after_index: int) -> set[str]:
    trailing = messages[after_index + 1 :]
    return {message.tool_call_id for message in trailing if isinstance(message, ToolMessage)}


def _require_tool_call_id(call: ToolCall) -> str:
    """Narrow ToolCall's `id: str | None` to `str`.

    Every tool_call this graph ever produces or scripts (ScriptedChatModel's
    builder, real providers) always assigns one; a missing id would mean
    there's no way to correlate the eventual ToolMessage back to this call.
    """
    call_id = call["id"]
    if call_id is None:  # pragma: no cover - defensive: our own model/providers always assign one
        raise InvalidToolCallError(f"Tool call for {call['name']!r} is missing an id.")
    return call_id


def _classify_unhandled_tool_calls(
    messages: Sequence[AnyMessage], tool_specs_by_name: dict[str, ToolSpec]
) -> tuple[list[ToolCall], list[ToolCall]]:
    """Split the most recent AIMessage's not-yet-answered tool_calls into (safe, effect).

    "Not yet answered" = no ToolMessage with a matching tool_call_id appears
    after it — which is what lets this same function correctly return "only
    the effect calls" once `safe_tools` has already run (docs/DESIGN.md §3:
    "safe 먼저 전부 실행 후 approval 진입").
    """
    ai_index = _find_last_ai_message_index(messages)
    if ai_index is None:  # pragma: no cover - defensive, see _find_last_ai_message_index
        return [], []
    last_ai_message = messages[ai_index]
    if not isinstance(last_ai_message, AIMessage) or not last_ai_message.tool_calls:
        return [], []
    answered = _answered_tool_call_ids(messages, ai_index)

    safe: list[ToolCall] = []
    effect: list[ToolCall] = []
    for call in last_ai_message.tool_calls:
        if call["id"] in answered:
            continue
        spec = tool_specs_by_name.get(call["name"])
        if spec is None:
            raise InvalidToolCallError(
                f"Model called unregistered tool {call['name']!r}. "
                f"Registered tools: {sorted(tool_specs_by_name)}."
            )
        (effect if spec.requires_approval else safe).append(call)
    return safe, effect


def _draft_from_args(args: dict[str, Any]) -> str:
    """A human-readable draft for the approval interrupt payload (DESIGN §3).

    v0.1's one effect tool (send_reorder_email) has a `body` arg that *is*
    the draft; anything without one falls back to a plain rendering of its
    args so this stays usable for a future effect tool with a different shape.
    """
    body = args.get("body")
    if isinstance(body, str):
        return body
    return json.dumps(args, ensure_ascii=False)


PREVIEW_CHARS = 120
"""Longest value `PendingAction.args_preview` keeps, per argument."""


def _summarize_args(args: dict[str, Any]) -> dict[str, JsonValue]:
    """The human-facing summary for `PendingAction.args_preview` (DESIGN §2).

    Scalars pass through; strings and nested values are cut to
    `PREVIEW_CHARS`, so the interrupt payload — checkpointed on every pause
    and returned by `/state` — stays small however long the model's
    arguments get (audit 001, AUD-005). The tool never runs from this
    summary: `effect_tools` uses `_original_tool_call`.
    """
    preview: dict[str, JsonValue] = {}
    for key, value in args.items():
        if value is None or isinstance(value, bool | int | float):
            preview[key] = value
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        preview[key] = text if len(text) <= PREVIEW_CHARS else text[: PREVIEW_CHARS - 1] + "…"
    return preview


def _original_tool_call(messages: Sequence[AnyMessage], tool_call_id: str) -> ToolCall:
    """The model's own tool_call for `tool_call_id`, from the last AIMessage —
    the arguments an approved effect actually runs with, in full.
    """
    ai_index = _find_last_ai_message_index(messages)
    last_ai_message = messages[ai_index] if ai_index is not None else None
    if isinstance(last_ai_message, AIMessage):
        for call in last_ai_message.tool_calls:
            if call["id"] == tool_call_id:
                return call
    # Defensive: `approval` always records a call taken from this very message.
    raise InvalidToolCallError(  # pragma: no cover
        f"Tool call {tool_call_id!r} is no longer in the last model message."
    )


async def _run_tool_call(
    tool: BaseTool, call: ToolCall, *, thread_id: str, clock: Clock
) -> ToolMessage:
    """Invoke one tool call, turning an exception into an error ToolMessage
    instead of crashing the graph (docs/TESTING.md §4), and log it.

    The model only sees `describe_error(exc)` — type and first line — never
    a traceback, request dump or path an SDK error may carry (audit 001,
    AUD-007); the full exception rides on the log record as `exc_info`.
    """
    started = clock()
    error: Exception | None = None
    try:
        result = await tool.ainvoke(call)
    except Exception as exc:  # broad on purpose: a tool failure becomes a ToolMessage, not a crash
        error = exc
        result = ToolMessage(
            content=f"Tool {call['name']!r} failed: {describe_error(exc)}",
            tool_call_id=_require_tool_call_id(call),
            name=call["name"],
        )
    fields: dict[str, Any] = {
        "thread_id": thread_id,
        "tool": call["name"],
        "tool_call_id": call["id"],
        "duration_ms": (clock() - started) * 1000,
        "ok": error is None,
    }
    if error is not None:
        fields["error_type"] = type(error).__name__
    logger.log(
        logging.INFO if error is None else logging.WARNING, "tool", extra=fields, exc_info=error
    )
    return result


def _instrumented(name: str, node: _Node, clock: Clock):
    """Wrap a node so each completed run logs `node` + `duration_ms`.

    The wrapper keeps the explicit `(state, config)` signature because
    LangGraph decides whether to inject `config` by inspecting it — and the
    return type is left to inference for the same reason: a bare
    `Callable[[AgentState, RunnableConfig], ...]` annotation erases the
    parameter *names*, which LangGraph's StateNode protocol matches on.
    A run that pauses at `interrupt()` unwinds by exception and logs
    nothing — the completed run after resume is the one that gets timed.
    """

    async def run(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        started = clock()
        result = await node(state, config)
        logger.info(
            "node",
            extra={
                "thread_id": _thread_id(config),
                "node": name,
                "duration_ms": (clock() - started) * 1000,
            },
        )
        return result

    return run


def build_graph(
    model: BaseChatModel,
    tool_specs: Sequence[ToolSpec],
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    *,
    clock: Clock = time.monotonic,
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    """Assemble and compile the v0.1 agent graph (DESIGN §3).

    `checkpointer` is optional only for ad-hoc/one-off use — pass one (see
    adapters/checkpoint.py) for anything that needs interrupts to survive
    past a single `ainvoke` call, which in practice means every real use.
    `clock` only feeds the structured logs' `duration_ms`.
    """
    tool_specs_by_name = {spec.tool.name: spec for spec in tool_specs}
    tools_by_name = {spec.tool.name: spec.tool for spec in tool_specs}
    bound_model = model.bind_tools([spec.tool for spec in tool_specs])

    async def agent_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        response = await bound_model.ainvoke(state["messages"])
        return {"messages": [response], "usage": usage_of_call(response)}

    def route_after_agent(state: AgentState) -> str:
        safe, effect = _classify_unhandled_tool_calls(state["messages"], tool_specs_by_name)
        if safe:
            return SAFE_TOOLS
        if effect:
            return APPROVAL
        return END

    async def safe_tools_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        safe, _effect = _classify_unhandled_tool_calls(state["messages"], tool_specs_by_name)
        thread_id = _thread_id(config)
        calls = (
            _run_tool_call(tools_by_name[call["name"]], call, thread_id=thread_id, clock=clock)
            for call in safe
        )
        results = await asyncio.gather(*calls)
        return {"messages": list(results)}

    def route_after_safe_tools(state: AgentState) -> str:
        _safe, effect = _classify_unhandled_tool_calls(state["messages"], tool_specs_by_name)
        return APPROVAL if effect else AGENT

    async def approval_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        _safe, effect = _classify_unhandled_tool_calls(state["messages"], tool_specs_by_name)
        # v0.1 supports at most one effect tool_call per turn — PendingAction
        # (core/state.py) is a single object, not a list, by design.
        call = effect[0]
        pending = PendingAction(
            tool_call_id=_require_tool_call_id(call),
            tool_name=call["name"],
            args_preview=_summarize_args(call["args"]),
        )
        resume = interrupt({"action": pending, "draft": _draft_from_args(call["args"])})

        if not resume.get("approved"):
            comment = resume.get("comment") or "No reason given."
            rejection = ToolMessage(
                content=f"Rejected by human reviewer: {comment}",
                tool_call_id=pending.tool_call_id,
                name=pending.tool_name,
            )
            return {"messages": [rejection], "pending": None}
        return {"pending": pending}

    def route_after_approval(state: AgentState) -> str:
        return EFFECT_TOOLS if state["pending"] is not None else AGENT

    async def effect_tools_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        pending = state["pending"]
        if pending is None:  # pragma: no cover - route_after_approval guarantees this
            raise AssertionError("effect_tools reached with no pending action")
        # Run the model's original call, not the preview: the preview is a
        # truncated summary for the human (DESIGN §2), never the arguments.
        call = _original_tool_call(state["messages"], pending.tool_call_id)
        message = await _run_tool_call(
            tools_by_name[pending.tool_name], call, thread_id=_thread_id(config), clock=clock
        )
        return {"messages": [message], "pending": None}

    builder = StateGraph(AgentState)
    builder.add_node(AGENT, _instrumented(AGENT, agent_node, clock))
    builder.add_node(SAFE_TOOLS, _instrumented(SAFE_TOOLS, safe_tools_node, clock))
    builder.add_node(APPROVAL, _instrumented(APPROVAL, approval_node, clock))
    builder.add_node(EFFECT_TOOLS, _instrumented(EFFECT_TOOLS, effect_tools_node, clock))

    builder.add_edge(START, AGENT)
    builder.add_conditional_edges(AGENT, route_after_agent, [SAFE_TOOLS, APPROVAL, END])
    builder.add_conditional_edges(SAFE_TOOLS, route_after_safe_tools, [APPROVAL, AGENT])
    builder.add_conditional_edges(APPROVAL, route_after_approval, [EFFECT_TOOLS, AGENT])
    builder.add_edge(EFFECT_TOOLS, AGENT)

    return builder.compile(checkpointer=checkpointer)
