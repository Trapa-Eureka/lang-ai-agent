"""Direct tests for core/graph.py's pure helpers not otherwise exercised by
the component-level golden-trajectory/approval-gate tests.
"""

from langchain_core.messages import AIMessage
from langchain_core.messages.ai import UsageMetadata

from lang_ai_agent.core.graph import (
    _draft_from_args,  # pyright: ignore[reportPrivateUsage]
    usage_after_call,
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


def test_usage_after_call_adds_the_response_tokens_and_counts_the_call() -> None:
    response = AIMessage(
        content="ok", usage_metadata=UsageMetadata(input_tokens=7, output_tokens=3, total_tokens=10)
    )

    total = usage_after_call(Usage(input_tokens=100, output_tokens=20, calls=4), response)

    assert total == Usage(input_tokens=107, output_tokens=23, calls=5)


def test_usage_after_call_without_metadata_only_counts_the_call() -> None:
    total = usage_after_call(
        Usage(input_tokens=1, output_tokens=1, calls=1), AIMessage(content="ok")
    )

    assert total == Usage(input_tokens=1, output_tokens=1, calls=2)
