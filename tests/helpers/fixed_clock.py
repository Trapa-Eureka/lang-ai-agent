"""FixedClock — deterministic time for duration assertions (docs/TESTING.md §2).

`build_graph(..., clock=FixedClock())` replaces `time.monotonic` in the
graph's structured logs. Every read advances the clock by a fixed `step`,
so a span that reads it twice (start/end) always measures exactly `step`
seconds, and a log-line snapshot can assert an exact `duration_ms` instead
of a fuzzy `> 0`.
"""


class FixedClock:
    def __init__(self, start: float = 1_000.0, step: float = 0.25) -> None:
        self._now = start
        self._step = step

    def __call__(self) -> float:
        now = self._now
        self._now += self._step
        return now
