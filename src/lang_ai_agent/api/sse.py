"""SSE event schema and the astream_events mapper (see docs/DESIGN.md §5).

This module defines the wire-level event types the `POST /threads/{id}/messages`
and `POST /threads/{id}/approve` endpoints stream to clients, and
`stream_sse_events()`, which drives a compiled graph and maps its
`astream_events` output into that schema, guaranteeing the event order
DESIGN promises: `token*` then `tool_start`/`tool_end`* then either
`interrupt` or `usage` -> `done`.
"""

# pyright: reportUnknownMemberType=false
# CompiledStateGraph.astream_events/.aget_state are overloaded heavily
# enough that pyright can't fully resolve them even for textbook-correct,
# fully-concrete usage (same finding as core/graph.py and
# tests/unit/test_checkpoint.py). Scoped to this file; every other file
# keeps the check.

import time
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Interrupt
from pydantic import BaseModel, Field

from lang_ai_agent.core.state import AgentState, PendingAction, Usage


class TokenEvent(BaseModel):
    """One model text delta."""

    type: Literal["token"] = "token"
    content: str


class ToolStartEvent(BaseModel):
    """A tool call has started executing.

    `tool_call_id` correlates this with its `ToolEndEvent` — it's
    `astream_events`' own run id for the invocation, not necessarily the
    model's tool_call.id (that id isn't available at start time; see
    `stream_sse_events`).
    """

    type: Literal["tool_start"] = "tool_start"
    tool_call_id: str
    tool_name: str


class ToolEndEvent(BaseModel):
    """A tool call has finished executing. See `ToolStartEvent.tool_call_id`."""

    type: Literal["tool_end"] = "tool_end"
    tool_call_id: str
    tool_name: str
    duration_ms: float


class InterruptEvent(BaseModel):
    """The graph has paused for human approval of an effect tool call."""

    type: Literal["interrupt"] = "interrupt"
    pending: PendingAction
    draft: str | None = None


class UsageEvent(BaseModel):
    """Cumulative token/call usage for the thread so far."""

    type: Literal["usage"] = "usage"
    usage: Usage


class DoneEvent(BaseModel):
    """The stream has finished normally."""

    type: Literal["done"] = "done"


class ErrorEvent(BaseModel):
    """The stream ended because of an error."""

    type: Literal["error"] = "error"
    message: str


type SSEEvent = Annotated[
    TokenEvent
    | ToolStartEvent
    | ToolEndEvent
    | InterruptEvent
    | UsageEvent
    | DoneEvent
    | ErrorEvent,
    Field(discriminator="type"),
]
"""Tagged union of every SSE event type, discriminated on `type`."""


def _first_interrupt(
    state_interrupts: tuple[Interrupt, ...],
) -> Interrupt | None:
    return state_interrupts[0] if state_interrupts else None


async def stream_sse_events(
    graph: CompiledStateGraph[AgentState, Any, AgentState, AgentState],
    graph_input: AgentState | Command[Any],
    config: RunnableConfig,
) -> AsyncIterator[SSEEvent]:
    """Drive `graph` and map its `astream_events` output to `SSEEvent`s.

    Order (DESIGN §5): `token*` (from `on_chat_model_stream` chunks with
    non-empty text — a non-streaming model just never produces any) then
    `tool_start`/`tool_end`* (one pair per tool invocation, correlated by
    `astream_events`' own run id — see `ToolStartEvent`) then, once the
    stream itself ends, either one `interrupt` (the graph paused for
    approval) or one `usage` followed by `done`. Any exception escaping the
    stream becomes a single `error` event instead of propagating — this is
    the boundary between "the graph failed" and "the client finds out".
    """
    tool_started_at: dict[str, float] = {}

    try:
        async for event in graph.astream_events(graph_input, config, version="v2"):
            match event["event"]:
                case "on_chat_model_stream":
                    chunk = event["data"].get("chunk")
                    content = chunk.content if chunk is not None else None
                    if isinstance(content, str) and content:
                        yield TokenEvent(content=content)
                case "on_tool_start":
                    run_id = str(event["run_id"])
                    tool_started_at[run_id] = time.monotonic()
                    yield ToolStartEvent(tool_call_id=run_id, tool_name=event["name"])
                case "on_tool_end":
                    run_id = str(event["run_id"])
                    started_at = tool_started_at.pop(run_id, None)
                    duration_ms = (time.monotonic() - started_at) * 1000 if started_at else 0.0
                    yield ToolEndEvent(
                        tool_call_id=run_id, tool_name=event["name"], duration_ms=duration_ms
                    )
                case _:
                    pass  # every other astream_events event is internal graph plumbing
    except Exception as exc:  # broad on purpose: any failure becomes one error event, not a crash
        yield ErrorEvent(message=str(exc))
        return

    state = await graph.aget_state(config)
    interrupt = _first_interrupt(state.interrupts)
    if interrupt is not None:
        yield InterruptEvent(pending=interrupt.value["action"], draft=interrupt.value.get("draft"))
        return
    yield UsageEvent(usage=state.values["usage"])
    yield DoneEvent()
