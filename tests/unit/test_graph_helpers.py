"""Direct tests for core/graph.py's pure helpers not otherwise exercised by
the component-level golden-trajectory/approval-gate tests.
"""

from lang_ai_agent.core.graph import _draft_from_args  # pyright: ignore[reportPrivateUsage]


def test_draft_from_args_uses_the_body_field_when_present() -> None:
    assert _draft_from_args({"to": "x@example.com", "body": "Please reorder."}) == "Please reorder."


def test_draft_from_args_falls_back_to_json_when_there_is_no_body() -> None:
    """Every v0.1 effect tool call has a `body`, but a future effect tool
    with a different args shape shouldn't crash the approval interrupt.
    """
    draft = _draft_from_args({"amount": 100, "currency": "USD"})

    assert draft == '{"amount": 100, "currency": "USD"}'
