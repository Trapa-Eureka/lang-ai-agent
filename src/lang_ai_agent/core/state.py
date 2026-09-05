"""Graph state schema.

State is serialized on every checkpoint (see docs/DESIGN.md §2), so it must
stay small: messages and minimal metadata only. Never stash a tool's raw,
large result here — summarize it into the ToolMessage content and keep the
original outside of state.
"""

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, JsonValue


class PendingAction(BaseModel):
    """A single effect tool call awaiting human approval.

    `args_preview` is what gets shown to the human at the approval
    interrupt — a short summary of the call's arguments (`core/graph.py`
    cuts every value to a preview length), never the payload itself, since
    this object round-trips through the checkpoint on every interrupt. It
    is *not* what the tool runs with: `effect_tools` re-reads the original
    tool_call from the messages by `tool_call_id` (audit 001, AUD-005).
    """

    tool_call_id: str
    tool_name: str
    args_preview: dict[str, JsonValue]


class Usage(BaseModel):
    """Token/call usage. In `AgentState` it is the thread's cumulative
    total; nodes emit one call's *delta* and `add_usage` folds it in.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0


def add_usage(current: Usage, update: Usage) -> Usage:
    """Reducer for `AgentState.usage` — the total only ever grows.

    With a plain last-value channel, the `Usage()` every `/messages`
    request submits as input silently reset the thread's total to zero at
    the start of each turn (audit 001, AUD-003). As a reducer, that input
    adds nothing and the total survives turns, interrupts and restarts.
    """
    return Usage(
        input_tokens=current.input_tokens + update.input_tokens,
        output_tokens=current.output_tokens + update.output_tokens,
        calls=current.calls + update.calls,
    )


class AgentState(TypedDict):
    """The graph's checkpointed state (see docs/DESIGN.md §2-3).

    Kept intentionally minimal — `messages`, one optional pending approval,
    and a running usage total. Do not add large or domain-specific payload
    fields here; this dict is serialized whole on every checkpoint write.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    pending: PendingAction | None
    usage: Annotated[Usage, add_usage]
