"""Scaffolding smoke tests for T0 — prove the test harness itself works.

Real agent-behavior tests start at T1+; this file exists only to satisfy
T0's completion criteria (docs/TASKS.md): "dummy async test runs".
"""

import asyncio

import lang_ai_agent


async def _identity(value: int) -> int:
    await asyncio.sleep(0)
    return value


async def test_dummy_async_harness_runs() -> None:
    """pytest-asyncio must run a bare `async def test_*` with no explicit marker."""
    assert await _identity(1) == 1


def test_package_is_importable_and_versioned() -> None:
    assert lang_ai_agent.__version__ == "0.1.0"
