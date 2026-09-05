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
- `create_default_app()` — reads `Settings` (`.env`) via `load_settings()`,
  which fails fast with a `ConfigError` on a missing bearer token or
  provider key (DESIGN §8.1), then opens the real model + AsyncSqliteSaver.
  `make dev` runs this through uvicorn's `--factory` flag and
  `lang-ai-agent serve` calls it directly, so nothing touches the
  environment at import time.
"""

import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sse_starlette.event import ServerSentEvent
from sse_starlette.sse import EventSourceResponse

from lang_ai_agent.adapters.builtin_tools import build_builtin_tool_specs
from lang_ai_agent.adapters.checkpoint import build_sqlite_checkpointer, thread_config
from lang_ai_agent.adapters.effects import Effects, SendMode
from lang_ai_agent.adapters.llm import build_chat_model, missing_key_error
from lang_ai_agent.adapters.mcp_loader import load_mcp_servers_config, load_mcp_tool_specs
from lang_ai_agent.adapters.observability import apply_langsmith_tracing, configure_logging
from lang_ai_agent.api.auth import require_bearer_token
from lang_ai_agent.api.sse import SSEEvent, content_text, stream_sse_events
from lang_ai_agent.core.graph import build_graph
from lang_ai_agent.core.state import AgentState, PendingAction, Usage
from lang_ai_agent.core.tools_spec import merge_tool_specs

type AgentGraph = CompiledStateGraph[AgentState, Any, AgentState, AgentState]
type GraphFactory = Callable[[], AbstractAsyncContextManager[AgentGraph]]


class Settings(BaseSettings):
    """The `.env` contract from docs/DESIGN.md §8 (see `.env.example`)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_bearer_token: str
    model: str | None = None
    checkpoint_db_path: str = "./data/checkpoints.db"
    send_mode: SendMode = SendMode.DRY_RUN
    langsmith_tracing: bool = False
    mcp_servers_path: str | None = None
    """Optional path to an `mcp_servers.json`; unset means built-in tools only."""


class ConfigError(ValueError):
    """`.env` / the environment can't start the server. The message names the
    cause and the fix (CLAUDE.md's error convention); `lang-ai-agent serve`
    prints it without a traceback.
    """


def load_settings() -> Settings:
    """`.env` → process environment → validated `Settings`, failing fast on
    what the first request would otherwise trip over (DESIGN §8.1).
    """
    # Export .env into the process environment *before* anything reads it.
    # pydantic-settings only loads .env into our Settings object; provider
    # clients (langchain-anthropic, -openai, ...) read their API key from
    # os.environ, so without this a .env-only ANTHROPIC_API_KEY silently
    # never reached the model — caught by the first real-model smoke. Real
    # environment variables still win: load_dotenv never overrides them.
    load_dotenv(".env")
    try:
        settings = Settings()  # pyright: ignore[reportCallIssue] - app_bearer_token comes from the env
    except ValidationError as e:
        fields = ", ".join(
            ".".join(str(part) for part in error["loc"]).upper() for error in e.errors()
        )
        raise ConfigError(
            f"Missing or invalid settings: {fields}.\n"
            "Fix: run `lang-ai-agent init` to write .env interactively, or copy "
            ".env.example to .env and fill in the blanks."
        ) from e
    problem = missing_key_error(settings.model)
    if problem is not None:
        raise ConfigError(problem)
    return settings


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
        last_text = content_text(messages[-1].content) if messages else ""
        interrupts = state.interrupts
        pending = interrupts[0].value["action"] if interrupts else state.values.get("pending")
        return ThreadState(
            thread_id=thread_id,
            message_count=len(messages),
            last_message=last_text or None,
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
async def open_default_graph(settings: Settings) -> AsyncGenerator[AgentGraph, None]:
    """The production graph for `settings`: real model, built-in (+ MCP)
    tools, AsyncSqliteSaver. Shared by the app and the real-model smoke.
    """
    effects = Effects(send_mode=settings.send_mode)
    model = build_chat_model(settings.model)
    tool_specs = build_builtin_tool_specs(effects)
    if settings.mcp_servers_path:
        # DESIGN §6: the real-MCP-server path. A tool name that collides with
        # a built-in (or another server's) tool is refused at startup rather
        # than silently shadowed.
        mcp_config = load_mcp_servers_config(Path(settings.mcp_servers_path))
        tool_specs = merge_tool_specs(tool_specs, await load_mcp_tool_specs(mcp_config))
    async with build_sqlite_checkpointer(settings.checkpoint_db_path) as checkpointer:
        yield build_graph(model, tool_specs, checkpointer=checkpointer)


def create_default_app() -> FastAPI:
    """The `make dev` / `lang-ai-agent serve` entry point: `Settings` from
    `.env` (fail-fast, see `load_settings`), real model, AsyncSqliteSaver.
    Reads the environment only when called, never on import.
    """
    settings = load_settings()
    configure_logging()
    apply_langsmith_tracing(settings.langsmith_tracing)
    return create_app(
        graph_factory=lambda: open_default_graph(settings),
        bearer_token=settings.app_bearer_token,
    )
