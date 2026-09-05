"""Scaffolding smoke tests for T0 — prove the test harness itself works.

Real agent-behavior tests start at T1+; this file exists only to satisfy
T0's completion criteria (docs/TASKS.md): "dummy async test runs".
"""

import asyncio
import tomllib
from pathlib import Path

import lang_ai_agent

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


async def _identity(value: int) -> int:
    await asyncio.sleep(0)
    return value


async def test_dummy_async_harness_runs() -> None:
    """pytest-asyncio must run a bare `async def test_*` with no explicit marker."""
    assert await _identity(1) == 1


def test_package_version_is_the_pyproject_version() -> None:
    """pyproject.toml is the single source of the version (docs/DESIGN.md §10);
    `__version__` must be read from it, never hardcoded to drift."""
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))

    assert lang_ai_agent.__version__ == pyproject["project"]["version"]
