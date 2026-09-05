"""Per-thread execution serialization (docs/DESIGN.md §5 — audit 001, AUD-001/002).

A thread's graph runs (`/messages`, `/approve`, `DELETE`) must not overlap:
two concurrent resumes of the same approval would each execute the effect
tool, and a message arriving mid-run would fork the checkpoint history.
`ThreadLocks` hands out one `asyncio.Lock` per thread_id, created on demand
and dropped once nobody holds or waits for it, so the table stays bounded
by the number of *active* threads rather than every thread ever seen.

Single-process by design: v0.1 runs one server on SQLite (SPEC §3).
Multi-process serialization needs a database lease plus effect idempotency
keys, which arrive with PostgresSaver in v0.2 (DESIGN §11).
"""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from lang_ai_agent.api.sse import ErrorEvent, SSEEvent


@dataclass
class _Entry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0
    """Holders plus waiters — the entry is dropped when this returns to zero."""


class ThreadLocks:
    """One `asyncio.Lock` per thread_id; see the module docstring."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    @property
    def active(self) -> int:
        """How many thread_ids currently have a holder or a waiter."""
        return len(self._entries)

    def is_busy(self, thread_id: str) -> bool:
        entry = self._entries.get(thread_id)
        return entry is not None and entry.lock.locked()

    @asynccontextmanager
    async def hold(self, thread_id: str) -> AsyncGenerator[None, None]:
        """Hold `thread_id`'s lock for the block; other holders wait in order."""
        entry = self._entries.setdefault(thread_id, _Entry())
        entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.users -= 1
            if entry.users == 0:
                self._entries.pop(thread_id, None)


async def serialized(
    locks: ThreadLocks,
    thread_id: str,
    *,
    precheck: Callable[[], Awaitable[str | None]],
    run: Callable[[], AsyncIterator[SSEEvent]],
) -> AsyncIterator[SSEEvent]:
    """Yield `run()`'s events while holding `thread_id`'s lock.

    `precheck` runs *inside* the lock, once any earlier run on the thread
    has finished. The handler's own check outside the lock decides the
    HTTP status (404/409); this one catches the race — a message queued
    behind a run that ended in an interrupt, or a second `/approve` for an
    approval the first one already consumed. Its message becomes a single
    `error` event and `run()` is never started.
    """
    async with locks.hold(thread_id):
        problem = await precheck()
        if problem is not None:
            yield ErrorEvent(message=problem)
            return
        async for event in run():
            yield event
