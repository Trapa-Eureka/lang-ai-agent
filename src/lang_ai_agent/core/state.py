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
    interrupt — keep it to a short, human-readable summary of the call's
    arguments (e.g. recipient + subject, not a full email body or a raw API
    payload). Large payloads belong in the tool's own draft/result content,
    never here, since this object round-trips through the checkpoint on
    every interrupt.
    """

    tool_call_id: str
    tool_name: str
    args_preview: dict[str, JsonValue]


class Usage(BaseModel):
    """Cumulative token/call usage for one thread, updated after each model call."""

    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0


class AgentState(TypedDict):
    """The graph's checkpointed state (see docs/DESIGN.md §2-3).

    Kept intentionally minimal — `messages`, one optional pending approval,
    and a running usage total. Do not add large or domain-specific payload
    fields here; this dict is serialized whole on every checkpoint write.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    pending: PendingAction | None
    usage: Usage
