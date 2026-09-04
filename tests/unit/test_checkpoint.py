# pyright: reportUnknownMemberType=false
# StateGraph.add_node/.compile and CompiledStateGraph.invoke are overloaded
# heavily enough that pyright can't fully resolve them even for
# textbook-correct, fully-concrete usage (confirmed with a minimal
# reproduction outside this file) — this isn't a gap in this file's own
# types. Scoped to this file rather than pyproject.toml's [tool.pyright] so
# the rest of the codebase keeps the check.
"""Tests for the checkpointer factory and thread_config (T4 completion criteria)."""

from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import TypeAdapter

from lang_ai_agent.adapters.checkpoint import (
    build_memory_checkpointer,
    build_sqlite_checkpointer,
    thread_config,
)


def test_thread_config_shape() -> None:
    assert thread_config("thread-1") == {"configurable": {"thread_id": "thread-1"}}


def test_memory_checkpointer_is_fresh_each_time() -> None:
    first = build_memory_checkpointer()
    second = build_memory_checkpointer()

    assert isinstance(first, InMemorySaver)
    assert first is not second


class _CounterState(TypedDict):
    count: int


def _bump(state: _CounterState) -> _CounterState:
    return {"count": state["count"] + 1}


def _compile_counter_graph(checkpointer: BaseCheckpointSaver[str]):
    """A minimal throwaway graph — this test is about the checkpointer
    adapter persisting correctly, not about lang_ai_agent's real
    AgentState/graph (T5's job, tested end to end there).
    """
    builder = StateGraph(_CounterState)
    builder.add_node("bump", _bump)
    builder.add_edge(START, "bump")
    builder.add_edge("bump", END)
    return builder.compile(checkpointer=checkpointer)


def test_sqlite_checkpointer_survives_a_simulated_restart(tmp_path: Path) -> None:
    """T4 completion criterion: temp-file SqliteSaver save/restore."""
    db_path = tmp_path / "checkpoints.db"
    config = thread_config("thread-1")

    with build_sqlite_checkpointer(str(db_path)) as checkpointer:
        graph = _compile_counter_graph(checkpointer)
        result = graph.invoke({"count": 0}, config)
        assert result == {"count": 1}
    # the `with` block has exited — this stands in for the server process
    # dying and being restarted, per docs/DESIGN.md's restart-resilience goal.

    assert db_path.exists()

    with build_sqlite_checkpointer(str(db_path)) as checkpointer:
        # a brand new checkpointer instance and a brand new compiled graph
        # object, pointing at the same db file and thread_id.
        graph = _compile_counter_graph(checkpointer)
        # get_state() hands back an untyped dict — this is a trust boundary
        # like any other (CLAUDE.md convention: parse it), not a place to
        # wave the mismatch through with a type: ignore.
        restored = TypeAdapter(_CounterState).validate_python(graph.get_state(config).values)
        assert restored == {"count": 1}

        result = graph.invoke(restored, config)
        assert result == {"count": 2}  # continues from the restored state


def test_sqlite_checkpointer_creates_missing_parent_directories(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "does" / "not" / "exist" / "checkpoints.db"

    with build_sqlite_checkpointer(str(db_path)):
        pass

    assert db_path.parent.is_dir()
