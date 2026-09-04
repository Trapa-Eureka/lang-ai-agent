# pyright: reportUnknownMemberType=false, reportUnusedFunction=false
# reportUnknownMemberType: CompiledStateGraph.aget_state is overloaded heavily
# enough that pyright can't fully resolve it even for textbook-correct usage
# (same finding as core/graph.py and api/sse.py).
# reportUnusedFunction: route handlers are registered by the @app decorators
# and never called by name — the canonical false positive for this check.
# Both scoped to this file.
"""FastAPI assembly (docs/DESIGN.md §5) — wiring only, no agent logic.

The four endpoints are thin: parse the request, find the thread's graph
state, hand off to `core.graph` / `api.sse`, frame the result. Everything
with behaviour worth testing on its own lives in those modules already.

Two ways to get an app:

- `create_app(graph_factory, bearer_token)` — explicit injection. Tests pass
  a ScriptedChatModel-backed graph and a known token.
- `create_default_app()` — reads `Settings` (`.env`) and opens the real
  model + AsyncSqliteSaver. `make dev` runs this through uvicorn's
  `--factory` flag so nothing touches the environment at import time.
"""

import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sse_starlette.event import ServerSentEvent
from sse_starlette.sse import EventSourceResponse

from lang_ai_agent.adapters.builtin_tools import build_builtin_tool_specs
from lang_ai_agent.adapters.checkpoint import build_sqlite_checkpointer, thread_config
from lang_ai_agent.adapters.effects import Effects, SendMode
from lang_ai_agent.adapters.llm import build_chat_model
from lang_ai_agent.api.auth import require_bearer_token
from lang_ai_agent.api.sse import SSEEvent, stream_sse_events
from lang_ai_agent.core.graph import build_graph
from lang_ai_agent.core.state import AgentState, PendingAction, Usage

type AgentGraph = CompiledStateGraph[AgentState, Any, AgentState, AgentState]
type GraphFactory = Callable[[], AbstractAsyncContextManager[AgentGraph]]


class Settings(BaseSettings):
    """The `.env` contract from docs/DESIGN.md §8 (see `.env.example`)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_bearer_token: str
    model: str | None = None
    checkpoint_db_path: str = "./data/checkpoints.db"
    send_mode: SendMode = SendMode.DRY_RUN


# --- request / response models --------------------------------------------


class ThreadCreated(BaseModel):
    thread_id: str


class MessageRequest(BaseModel):
    content: str = Field(min_length=1)


class ApproveRequest(BaseModel):
    approved: bool
    comment: str | None = None


class ThreadState(BaseModel):
    """`GET /threads/{id}/state` — a summary, not the message history
    (CLAUDE.md: keep payloads small; the full transcript is not an API
    concern in v0.1).
    """

    thread_id: str
    message_count: int
    last_message: str | None
    pending: PendingAction | None
    usage: Usage
    awaiting_approval: bool


# --- assembly ---------------------------------------------------------------


def _sse(events: AsyncIterator[SSEEvent]) -> AsyncGenerator[ServerSentEvent, None]:
    async def _frames() -> AsyncGenerator[ServerSentEvent, None]:
        async for event in events:
            yield ServerSentEvent(event=event.type, data=event.model_dump_json())

    return _frames()


def create_app(graph_factory: GraphFactory, bearer_token: str) -> FastAPI:
    """Assemble the API around a graph that `graph_factory` opens for the
    app's lifetime (the checkpointer behind it is an async context manager).
    """
    authed = Depends(require_bearer_token(bearer_token))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        async with graph_factory() as graph:
            app.state.graph = graph
            yield

    app = FastAPI(title="lang_ai_agent", lifespan=lifespan)

    def graph_of(request: Request) -> AgentGraph:
        return request.app.state.graph

    async def existing_state(graph: AgentGraph, thread_id: str) -> Any:
        state = await graph.aget_state(thread_config(thread_id))
        if not state.values:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Thread {thread_id!r} has no history. Create one with POST /threads, "
                    "then send it a message with POST /threads/{id}/messages first."
                ),
            )
        return state

    @app.post("/threads", response_model=ThreadCreated, dependencies=[authed])
    async def create_thread() -> ThreadCreated:
        # A thread only comes into existence in the checkpointer on its first
        # message; this just hands out a fresh, unique id for the client to use.
        return ThreadCreated(thread_id=str(uuid.uuid4()))

    @app.post("/threads/{thread_id}/messages", dependencies=[authed])
    async def send_message(
        thread_id: str, body: MessageRequest, request: Request
    ) -> EventSourceResponse:
        initial: AgentState = {
            "messages": [HumanMessage(content=body.content)],
            "pending": None,
            "usage": Usage(),
        }
        events = stream_sse_events(graph_of(request), initial, thread_config(thread_id))
        return EventSourceResponse(_sse(events))

    @app.get("/threads/{thread_id}/state", response_model=ThreadState, dependencies=[authed])
    async def get_state(thread_id: str, request: Request) -> ThreadState:
        state = await existing_state(graph_of(request), thread_id)
        messages = state.values.get("messages", [])
        last = messages[-1].content if messages else None
        interrupts = state.interrupts
        pending = interrupts[0].value["action"] if interrupts else state.values.get("pending")
        return ThreadState(
            thread_id=thread_id,
            message_count=len(messages),
            last_message=last if isinstance(last, str) else None,
            pending=pending,
            usage=state.values.get("usage", Usage()),
            awaiting_approval=bool(interrupts),
        )

    @app.post("/threads/{thread_id}/approve", dependencies=[authed])
    async def approve(
        thread_id: str, body: ApproveRequest, request: Request
    ) -> EventSourceResponse:
        graph = graph_of(request)
        state = await existing_state(graph, thread_id)
        if not state.interrupts:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Thread {thread_id!r} is not waiting for approval — there's nothing to "
                    "approve or reject. Send it a message that triggers an effect tool first."
                ),
            )
        resume = Command(resume={"approved": body.approved, "comment": body.comment})
        events = stream_sse_events(graph, resume, thread_config(thread_id))
        return EventSourceResponse(_sse(events))

    return app


@asynccontextmanager
async def _open_default_graph(settings: Settings) -> AsyncGenerator[AgentGraph, None]:
    effects = Effects(send_mode=settings.send_mode)
    model = build_chat_model(settings.model)
    async with build_sqlite_checkpointer(settings.checkpoint_db_path) as checkpointer:
        yield build_graph(model, build_builtin_tool_specs(effects), checkpointer=checkpointer)


def create_default_app() -> FastAPI:
    """The `make dev` entry point: `Settings` from `.env`, real model,
    AsyncSqliteSaver. Reads the environment only when called, never on import.
    """
    settings = Settings()  # pyright: ignore[reportCallIssue] - app_bearer_token comes from the env
    return create_app(
        graph_factory=lambda: _open_default_graph(settings),
        bearer_token=settings.app_bearer_token,
    )
