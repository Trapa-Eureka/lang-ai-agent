"""Checkpointer factory and thread-config utility (docs/DESIGN.md §1, §3, §8).

`InMemorySaver` for tests (docs/TESTING.md §1 — no persistence needed, and a
fresh instance per test avoids state leaking between tests). `AsyncSqliteSaver`
for dev/single-server v0.1 (docs/SPEC.md §3 — PostgresSaver and horizontal
scaling are v0.2).

Async, not the sync `SqliteSaver`: the graph (core/graph.py, T5) runs
async — its nodes call `ainvoke`/tools' async paths — and the sync
`SqliteSaver` raises `NotImplementedError` the moment an async graph method
touches it (confirmed empirically; DESIGN originally just said "SqliteSaver"
without anticipating this split, corrected here).
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


def _serde() -> JsonPlusSerializer:
    """A serializer that explicitly allow-lists lang_ai_agent's own
    checkpointed types: both `AgentState` fields that are plain Pydantic
    models rather than one of LangGraph's own message/checkpoint types.

    Passing an *explicit* `allowed_msgpack_modules` list switches the
    serde from "warn on any unregistered type" to "block anything not on
    this list" — so every such type has to be listed here, not just the
    first one that happened to need it (confirmed the hard way: adding
    only PendingAction here left `Usage` silently blocked). Leaving either
    unregistered would silently break restart resilience (DESIGN's whole
    reason for a real checkpointer) once deserialization is actually
    refused rather than just logged.
    """
    return JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("lang_ai_agent.core.state", "PendingAction"),
            ("lang_ai_agent.core.state", "Usage"),
        ]
    )


def build_memory_checkpointer() -> InMemorySaver:
    """A fresh, empty in-memory checkpointer — tests only (docs/TESTING.md §1)."""
    return InMemorySaver(serde=_serde())


@asynccontextmanager
async def build_sqlite_checkpointer(db_path: str) -> AsyncGenerator[AsyncSqliteSaver, None]:
    """An `AsyncSqliteSaver` backed by `db_path` (`.env`'s `CHECKPOINT_DB_PATH`).

    An async context manager — keep the `async with` block open for as long
    as a graph using this checkpointer is in use. Creates `db_path`'s
    parent directory if it doesn't exist yet, so a fresh checkout doesn't
    fail on a missing `data/` (`.gitignore`'d — see T0) with a bare
    sqlite3 "unable to open database file" error.

    Connects directly with `aiosqlite` (mirroring what
    `AsyncSqliteSaver.from_conn_string` does internally) rather than using
    that classmethod, because it doesn't take a `serde` argument and this
    needs the same allow-listed serde as `build_memory_checkpointer`.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as conn:
        yield AsyncSqliteSaver(conn, serde=_serde())


def thread_config(thread_id: str) -> RunnableConfig:
    """The per-thread graph invocation config (DESIGN §3)."""
    return {"configurable": {"thread_id": thread_id}}
