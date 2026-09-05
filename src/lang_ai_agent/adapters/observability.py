"""Observability plumbing (docs/DESIGN.md §7): structured JSON logs and the
LangSmith tracing switch.

The graph emits its log records with structured fields in `extra=`
(thread_id, node, tool, duration_ms — see core/graph.py); this module only
decides how those records are *rendered* and installs that once for the
`make dev` process. Tests read the same records straight from `caplog`, so
nothing here needs to be configured for the suite to observe the graph.
"""

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

# Everything a LogRecord carries by default (varies by Python version —
# 3.12 added `taskName`), captured from a throwaway record so the JSON
# formatter can treat any *other* attribute as a structured field the
# caller passed via `extra=`.
_STANDARD_RECORD_KEYS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
}

_HANDLER_TAG = "_lang_ai_agent_json_handler"


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, message, plus every
    structured field the record was given via `extra=`.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in _STANDARD_RECORD_KEYS
            }
        )
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int = logging.INFO) -> logging.Handler:
    """Install a JSON handler on the root logger (idempotent — a second call
    replaces the first one's handler rather than stacking a duplicate).
    """
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not getattr(h, _HANDLER_TAG, False)]
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_TAG, True)
    root.addHandler(handler)
    root.setLevel(level)
    return handler


def apply_langsmith_tracing(enabled: bool) -> None:
    """Export `LANGSMITH_TRACING` to the process environment.

    LangSmith reads that variable itself (confirmed against the installed
    `langsmith.utils.tracing_is_enabled`), but pydantic-settings only loads
    `.env` into our `Settings` object — it never exports to `os.environ`.
    Without this hop a `.env`-only `LANGSMITH_TRACING=true` would silently
    do nothing.
    """
    os.environ["LANGSMITH_TRACING"] = "true" if enabled else "false"
