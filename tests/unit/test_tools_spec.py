"""Tests for the ToolSpec safe/effect classification wrapper (T1)."""

from langchain_core.tools import tool

from lang_ai_agent.core.tools_spec import ToolSpec


@tool
def _check_stockout(store: str) -> str:
    """Fake safe tool used only to exercise ToolSpec in this test."""
    return f"no stockouts at {store}"


@tool
def _send_reorder_email(to: str, subject: str) -> str:
    """Fake effect tool used only to exercise ToolSpec in this test."""
    return f"sent to {to}: {subject}"


def test_tool_spec_wraps_a_safe_tool() -> None:
    spec = ToolSpec(tool=_check_stockout, requires_approval=False)

    assert spec.requires_approval is False
    assert spec.tool.name == "_check_stockout"


def test_tool_spec_wraps_an_effect_tool() -> None:
    spec = ToolSpec(tool=_send_reorder_email, requires_approval=True)

    assert spec.requires_approval is True
    assert spec.tool.name == "_send_reorder_email"


def test_tool_spec_preserves_tool_invocation() -> None:
    spec = ToolSpec(tool=_check_stockout, requires_approval=False)

    result = spec.tool.invoke({"store": "downtown"})

    assert result == "no stockouts at downtown"
