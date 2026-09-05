"""Tests for adapters/observability.py (T8): the JSON log formatter, the
idempotent handler install, and the LangSmith tracing env wiring.
"""

import json
import logging

import pytest

from lang_ai_agent.adapters.observability import (
    JsonFormatter,
    apply_langsmith_tracing,
    configure_logging,
)


def _record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("lang_ai_agent.graph", logging.INFO, __file__, 1, message, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formatter_emits_one_json_object_with_structured_fields() -> None:
    line = JsonFormatter().format(
        _record("tool", thread_id="t-1", tool="check_stockout", duration_ms=250.0, ok=True)
    )

    payload = json.loads(line)
    assert payload["message"] == "tool"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "lang_ai_agent.graph"
    assert payload["thread_id"] == "t-1"
    assert payload["tool"] == "check_stockout"
    assert payload["duration_ms"] == 250.0
    assert payload["ok"] is True
    assert payload["ts"].endswith("+00:00")  # UTC, ISO-8601


def test_formatter_keeps_non_ascii_and_stringifies_odd_values() -> None:
    line = JsonFormatter().format(_record("node", node="approval", note="승인 대기", when={1, 2}))

    assert "승인 대기" in line  # ensure_ascii=False
    assert json.loads(line)["when"] == "{1, 2}"  # default=str, never a crash


def test_formatter_includes_exception_text() -> None:
    try:
        raise RuntimeError("kaboom")
    except RuntimeError:
        record = _record("tool", tool="boom")
        record.exc_info = __import__("sys").exc_info()

    assert "kaboom" in json.loads(JsonFormatter().format(record))["exc_info"]


def test_configure_logging_is_idempotent() -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        first = configure_logging()
        second = configure_logging()

        ours = [h for h in root.handlers if h in (first, second)]
        assert ours == [second]  # the first install was replaced, not stacked
        assert isinstance(second.formatter, JsonFormatter)
    finally:
        root.handlers = before


@pytest.mark.parametrize(("enabled", "expected"), [(True, "true"), (False, "false")])
def test_apply_langsmith_tracing_exports_the_env_var(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, expected: str
) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)

    apply_langsmith_tracing(enabled)

    assert __import__("os").environ["LANGSMITH_TRACING"] == expected
