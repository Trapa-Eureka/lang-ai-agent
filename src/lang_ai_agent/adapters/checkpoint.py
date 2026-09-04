"""Checkpointer factory and thread-config utility (docs/DESIGN.md §1, §3, §8).

`InMemorySaver` for tests (docs/TESTING.md §1 — no persistence needed, and a
fresh instance per test avoids state leaking between tests). `SqliteSaver`
for dev/single-server v0.1 (docs/SPEC.md §3 — PostgresSaver and horizontal
scaling are v0.2).
"""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver


def build_memory_checkpointer() -> InMemorySaver:
    """A fresh, empty in-memory checkpointer — tests only (docs/TESTING.md §1)."""
    return InMemorySaver()


@contextmanager
def build_sqlite_checkpointer(db_path: str) -> Generator[SqliteSaver, None, None]:
    """A `SqliteSaver` backed by `db_path` (`.env`'s `CHECKPOINT_DB_PATH`).

    A context manager, matching `SqliteSaver.from_conn_string`'s own shape —
    keep the `with` block open for as long as a graph using this
    checkpointer is in use. Creates `db_path`'s parent directory if it
    doesn't exist yet, so a fresh checkout doesn't fail on a missing `data/`
    (`.gitignore`'d — see T0) with a bare sqlite3 "unable to open database
    file" error.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(db_path) as saver:
        yield saver


def thread_config(thread_id: str) -> RunnableConfig:
    """The per-thread graph invocation config (DESIGN §3)."""
    return {"configurable": {"thread_id": thread_id}}
