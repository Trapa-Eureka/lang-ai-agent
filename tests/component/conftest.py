# pyright: reportUnknownMemberType=false
# CompiledStateGraph.astream/.aget_state are overloaded heavily enough that
# pyright can't fully resolve them even for textbook-correct, fully-concrete
# usage (same finding as core/graph.py and tests/unit/test_checkpoint.py).
# Scoped to this file; every other file keeps the check.
"""Shared fixtures for graph-level component tests (docs/TESTING.md §3-4)."""

from collections.abc import Sequence
from typing import Any, Protocol

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from lang_ai_agent.adapters.builtin_tools import build_builtin_tool_specs
from lang_ai_agent.adapters.checkpoint import build_memory_checkpointer, thread_config
from lang_ai_agent.adapters.effects import SendMode
from lang_ai_agent.api.sse import SSEEvent, stream_sse_events
from lang_ai_agent.core.graph import build_graph
from lang_ai_agent.core.state import AgentState, Usage
from tests.helpers.mock_effects import MockEffects
from tests.helpers.scripted_chat_model import ScriptedChatModel


class GraphHarness:
    """A compiled test graph plus everything needed to inspect its run.

    `checkpointer` is exposed (not just used internally) so a test can
    discard the graph and rebuild a fresh one against the same
    checkpointer/thread to simulate a restart (docs/TESTING.md's restart
    scenarios) — not exercised by every test, but available where needed.
    """

    def __init__(
        self,
        script_messages: Sequence[AIMessage],
        send_mode: SendMode = SendMode.DRY_RUN,
        checkpointer: InMemorySaver | None = None,
    ) -> None:
        self.model = ScriptedChatModel(script=list(script_messages))
        self.effects = MockEffects(send_mode=send_mode)
        self.tool_specs = build_builtin_tool_specs(self.effects)
        self.checkpointer = checkpointer or build_memory_checkpointer()
        self.graph = build_graph(self.model, self.tool_specs, checkpointer=self.checkpointer)
        self.config = thread_config("test-thread")

    def rebuild(self) -> None:
        """Recompile a fresh graph object against the same checkpointer —
        stands in for a server restart (a brand new process, same disk
        state) without needing a real file-backed checkpointer.
        """
        self.graph = build_graph(self.model, self.tool_specs, checkpointer=self.checkpointer)

    async def run(self, content: str) -> tuple[list[str], dict[str, Any]]:
        """Start a new turn. Returns (visited_nodes, final_result_or_interrupt_info)."""
        initial_state: AgentState = {
            "messages": [HumanMessage(content=content)],
            "pending": None,
            "usage": Usage(),
        }
        visited: list[str] = []
        result: dict[str, Any] = {}
        async for chunk in self.graph.astream(
            initial_state,
            self.config,
            stream_mode="updates",
        ):
            if "__interrupt__" in chunk:
                result["interrupt"] = chunk["__interrupt__"][0]
                continue
            visited.extend(chunk.keys())
            result.update(chunk)
        return visited, result

    async def resume(
        self, approved: bool, comment: str | None = None
    ) -> tuple[list[str], dict[str, Any]]:
        """Resume an interrupted turn. Same return shape as `run`."""
        visited: list[str] = []
        result: dict[str, Any] = {}
        async for chunk in self.graph.astream(
            Command(resume={"approved": approved, "comment": comment}),
            self.config,
            stream_mode="updates",
        ):
            visited.extend(chunk.keys())
            result.update(chunk)
        return visited, result

    async def state_values(self) -> dict[str, Any]:
        state = await self.graph.aget_state(self.config)
        return state.values

    async def sse_run(self, content: str) -> list[SSEEvent]:
        """Like `run`, but through the SSE mapper (T6) instead of raw astream."""
        initial_state: AgentState = {
            "messages": [HumanMessage(content=content)],
            "pending": None,
            "usage": Usage(),
        }
        return [event async for event in stream_sse_events(self.graph, initial_state, self.config)]

    async def sse_resume(self, approved: bool, comment: str | None = None) -> list[SSEEvent]:
        """Like `resume`, but through the SSE mapper (T6) instead of raw astream."""
        resume_command = Command(resume={"approved": approved, "comment": comment})
        return [event async for event in stream_sse_events(self.graph, resume_command, self.config)]


class MakeHarness(Protocol):
    """Type of the `make_harness` fixture — a `Protocol` (rather than a bare
    `Callable[..., GraphHarness]`) so `send_mode`'s default is visible to
    pyright at every call site, in every test file that takes this fixture
    as a parameter.
    """

    def __call__(
        self, script_messages: Sequence[AIMessage], send_mode: SendMode = SendMode.DRY_RUN
    ) -> GraphHarness: ...


@pytest.fixture
def make_harness() -> MakeHarness:
    """Factory fixture: `make_harness(script_messages, send_mode=...)` builds
    a fresh GraphHarness (its own model/effects/checkpointer/graph/thread),
    so tests never share state with each other.
    """

    def _make(
        script_messages: Sequence[AIMessage], send_mode: SendMode = SendMode.DRY_RUN
    ) -> GraphHarness:
        return GraphHarness(script_messages, send_mode=send_mode)

    return _make
