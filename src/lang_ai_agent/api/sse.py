"""SSE event schema (see docs/DESIGN.md §5).

This module defines the wire-level event types the `POST /threads/{id}/messages`
and `POST /threads/{id}/approve` endpoints stream to clients. The mapping
from `astream_events` to these types (and the ordering guarantee: `token*`
then `tool_start`/`tool_end`* then either `interrupt` or `usage` -> `done`)
is T6's job — this module only pins the schema down as Pydantic so it can be
validated and tested on its own.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from lang_ai_agent.core.state import PendingAction, Usage


class TokenEvent(BaseModel):
    """One model text delta."""

    type: Literal["token"] = "token"
    content: str


class ToolStartEvent(BaseModel):
    """A tool call has started executing."""

    type: Literal["tool_start"] = "tool_start"
    tool_call_id: str
    tool_name: str


class ToolEndEvent(BaseModel):
    """A tool call has finished executing."""

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
