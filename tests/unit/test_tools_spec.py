"""Tests for the ToolSpec safe/effect classification wrapper (T1) and
merge_tool_specs (T9)."""

import pytest
from langchain_core.tools import tool

from lang_ai_agent.core.tools_spec import ToolSpec, merge_tool_specs


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


# --- merge_tool_specs (T9) --------------------------------------------------


def test_merge_tool_specs_concatenates_groups_in_order() -> None:
    safe = ToolSpec(tool=_check_stockout, requires_approval=False)
    effect = ToolSpec(tool=_send_reorder_email, requires_approval=True)

    merged = merge_tool_specs([safe], [effect])

    assert [s.tool.name for s in merged] == ["_check_stockout", "_send_reorder_email"]
    assert [s.requires_approval for s in merged] == [False, True]


def test_merge_tool_specs_refuses_a_duplicate_tool_name() -> None:
    """A duplicate would let the later spec silently shadow the earlier one's
    approval requirement in the graph's by-name lookup — fail loudly instead.
    """
    builtin = ToolSpec(tool=_check_stockout, requires_approval=False)
    shadowing = ToolSpec(tool=_check_stockout, requires_approval=True)

    with pytest.raises(ValueError, match=r"Duplicate tool name.*_check_stockout"):
        merge_tool_specs([builtin], [shadowing])


def test_merge_tool_specs_of_nothing_is_empty() -> None:
    assert merge_tool_specs() == []
