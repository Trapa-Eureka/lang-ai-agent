"""core/errors.py — what a client or the model may see of an exception
(docs/DESIGN.md §5/§7 — audit 001, AUD-007)."""

from lang_ai_agent.core.errors import MAX_ERROR_CHARS, describe_error


def test_describe_error_keeps_the_type_and_the_first_line_only() -> None:
    exc = RuntimeError(
        "connection refused\n"
        "POST https://api.example.com/v1/messages with key sk-not-for-clients\n"
        '  File "/home/me/app.py", line 1'
    )

    assert describe_error(exc) == "RuntimeError: connection refused"


def test_describe_error_caps_the_line_length() -> None:
    message = describe_error(ValueError("x" * 1000))

    assert message.startswith("ValueError: ")
    assert len(message) == len("ValueError: ") + MAX_ERROR_CHARS
    assert message.endswith("…")


def test_describe_error_without_a_message_is_just_the_type() -> None:
    assert describe_error(RuntimeError()) == "RuntimeError"
    assert describe_error(RuntimeError("   \n  ")) == "RuntimeError"
