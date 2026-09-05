"""Direct tests for core/graph.py's pure helpers not otherwise exercised by
the component-level golden-trajectory/approval-gate tests.
"""

from langchain_core.messages import AIMessage
from langchain_core.messages.ai import UsageMetadata

from lang_ai_agent.core.graph import (
    PREVIEW_CHARS,
    _draft_from_args,  # pyright: ignore[reportPrivateUsage]
    _summarize_args,  # pyright: ignore[reportPrivateUsage]
    usage_of_call,
)
from lang_ai_agent.core.state import Usage


def test_draft_from_args_uses_the_body_field_when_present() -> None:
    assert _draft_from_args({"to": "x@example.com", "body": "Please reorder."}) == "Please reorder."


def test_draft_from_args_falls_back_to_json_when_there_is_no_body() -> None:
    """Every v0.1 effect tool call has a `body`, but a future effect tool
    with a different args shape shouldn't crash the approval interrupt.
    """
    draft = _draft_from_args({"amount": 100, "currency": "USD"})

    assert draft == '{"amount": 100, "currency": "USD"}'


def test_usage_of_call_reads_the_response_tokens_and_counts_one_call() -> None:
    response = AIMessage(
        content="ok", usage_metadata=UsageMetadata(input_tokens=7, output_tokens=3, total_tokens=10)
    )

    assert usage_of_call(response) == Usage(input_tokens=7, output_tokens=3, calls=1)


def test_usage_of_call_without_metadata_only_counts_the_call() -> None:
    assert usage_of_call(AIMessage(content="ok")) == Usage(input_tokens=0, output_tokens=0, calls=1)


def test_summarize_args_keeps_scalars_and_cuts_long_or_nested_values() -> None:
    """PendingAction.args_preview is a bounded summary (DESIGN §2): the
    interrupt payload is checkpointed and returned by /state, so no single
    argument may drag the model's whole email body along (audit 001)."""
    long_body = "x" * (PREVIEW_CHARS + 50)

    preview = _summarize_args(
        {
            "to": "ops@example.com",
            "n": 3,
            "ratio": 0.5,
            "flag": True,
            "none": None,
            "body": long_body,
            "nested": {"k": "v"},
        }
    )

    assert preview["to"] == "ops@example.com"
    assert preview["n"] == 3 and preview["ratio"] == 0.5 and preview["flag"] is True
    assert preview["none"] is None
    assert preview["nested"] == '{"k": "v"}'  # nested values render as JSON, then get cut too
    body = preview["body"]
    assert isinstance(body, str)
    assert len(body) == PREVIEW_CHARS and body.endswith("…")
    assert body.startswith("x" * (PREVIEW_CHARS - 1))
