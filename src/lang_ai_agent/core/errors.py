"""Error-message sanitization (docs/DESIGN.md §5, §7 — audit 001, AUD-007).

What a client (the SSE `error` event) or the model (an error ToolMessage)
gets to see of an exception is `describe_error()`: the exception's class
name plus the first line of its message, capped — enough to react to, never
the traceback, request dump, file path or environment detail an SDK or MCP
error can carry. The full exception belongs in the server log, attached as
`exc_info` to a record that names the thread and tool it came from.
"""

MAX_ERROR_CHARS = 200
"""Longest message line `describe_error` passes through, in characters."""


def describe_error(exc: BaseException, *, limit: int = MAX_ERROR_CHARS) -> str:
    """`"TypeName: first line of the message"`, the line cut to `limit`
    characters; just `"TypeName"` when the exception has no message.
    """
    text = str(exc).strip()
    first_line = text.splitlines()[0].strip() if text else ""
    if len(first_line) > limit:
        first_line = first_line[: limit - 1] + "…"
    name = type(exc).__name__
    return f"{name}: {first_line}" if first_line else name
