"""api/thread_locks.py — one run per thread at a time (docs/DESIGN.md §5 —
audit 001, AUD-001/002)."""

import asyncio
from collections.abc import AsyncIterator

from lang_ai_agent.api.sse import ErrorEvent, SSEEvent, TokenEvent
from lang_ai_agent.api.thread_locks import ThreadLocks, serialized


async def test_hold_serializes_runs_on_the_same_thread_and_cleans_up() -> None:
    locks = ThreadLocks()
    order: list[str] = []

    async def worker(name: str) -> None:
        async with locks.hold("t"):
            order.append(f"{name}:in")
            await asyncio.sleep(0.01)
            order.append(f"{name}:out")

    await asyncio.gather(worker("a"), worker("b"))

    assert order == ["a:in", "a:out", "b:in", "b:out"]
    assert locks.active == 0  # nothing left behind once both are done


async def test_different_threads_run_concurrently() -> None:
    locks = ThreadLocks()
    a_inside = asyncio.Event()
    b_inside = asyncio.Event()

    async def worker(thread_id: str, mine: asyncio.Event, other: asyncio.Event) -> None:
        async with locks.hold(thread_id):
            mine.set()
            # would deadlock if t1 and t2 shared a lock
            await asyncio.wait_for(other.wait(), timeout=1)

    await asyncio.gather(worker("t1", a_inside, b_inside), worker("t2", b_inside, a_inside))

    assert locks.active == 0


async def test_is_busy_reflects_a_held_lock() -> None:
    locks = ThreadLocks()
    assert locks.is_busy("t") is False

    async with locks.hold("t"):
        assert locks.is_busy("t") is True
        assert locks.active == 1

    assert locks.is_busy("t") is False
    assert locks.active == 0


async def test_serialized_yields_the_run_inside_the_lock() -> None:
    locks = ThreadLocks()
    busy_during_run: list[bool] = []

    async def run() -> AsyncIterator[SSEEvent]:
        busy_during_run.append(locks.is_busy("t"))
        yield TokenEvent(content="hi")

    async def ok() -> str | None:
        return None

    events = [e async for e in serialized(locks, "t", precheck=ok, run=run)]

    assert events == [TokenEvent(content="hi")]
    assert busy_during_run == [True]
    assert locks.active == 0


async def test_serialized_precheck_failure_is_one_error_event_and_no_run() -> None:
    locks = ThreadLocks()
    started: list[bool] = []

    async def run() -> AsyncIterator[SSEEvent]:
        started.append(True)
        yield TokenEvent(content="never")

    async def lost_the_race() -> str | None:
        return "already handled"

    events = [e async for e in serialized(locks, "t", precheck=lost_the_race, run=run)]

    assert events == [ErrorEvent(message="already handled")]
    assert started == []
    assert locks.active == 0
