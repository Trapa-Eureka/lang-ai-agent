"""Tests for the v0.1 built-in fake tools (T3 completion criteria)."""

import pytest
from pydantic import ValidationError

from lang_ai_agent.adapters.builtin_tools import (
    CheckStockoutArgs,
    GetReorderSuggestionsArgs,
    build_builtin_tool_specs,
    make_check_stockout,
    make_get_reorder_suggestions,
    make_send_reorder_email,
)
from lang_ai_agent.adapters.effects import SendMode
from tests.helpers.mock_effects import MockEffects

# --- args_schema validation (Pydantic, "zod급 검증") -------------------------


def test_check_stockout_args_schema_rejects_wrong_types() -> None:
    tool = make_check_stockout()

    with pytest.raises(ValidationError):
        tool.invoke({"store": "main", "days_ahead": "not-a-number"})


def test_check_stockout_args_schema_rejects_out_of_range_days_ahead() -> None:
    tool = make_check_stockout()

    with pytest.raises(ValidationError):
        tool.invoke({"store": "main", "days_ahead": 0})  # ge=1


def test_send_reorder_email_args_schema_requires_all_fields() -> None:
    tool = make_send_reorder_email(MockEffects())

    with pytest.raises(ValidationError):
        tool.invoke({"to": "ops@example.com"})  # missing subject, body


# --- fixed, deterministic responses -----------------------------------------


def test_check_stockout_returns_fixed_data_for_a_known_store() -> None:
    tool = make_check_stockout()

    result = tool.invoke({"store": "main"})

    assert result == [
        {"sku": "SKU-100", "name": "Espresso Beans 1kg", "days_until_stockout": 3},
        {"sku": "SKU-142", "name": "Oat Milk 1L", "days_until_stockout": 5},
    ]


def test_check_stockout_respects_the_lookahead_window() -> None:
    tool = make_check_stockout()

    result = tool.invoke({"store": "main", "days_ahead": 3})

    assert [item["sku"] for item in result] == ["SKU-100"]


def test_check_stockout_returns_empty_for_an_unknown_store() -> None:
    tool = make_check_stockout()

    assert tool.invoke({"store": "nowhere"}) == []


def test_get_reorder_suggestions_returns_fixed_data() -> None:
    tool = make_get_reorder_suggestions()

    result = tool.invoke({"store": "main"})

    assert result == [
        {
            "sku": "SKU-100",
            "name": "Espresso Beans 1kg",
            "suggested_quantity": 35,
            "supplier": "Roastworks Co.",
        },
        {
            "sku": "SKU-142",
            "name": "Oat Milk 1L",
            "suggested_quantity": 25,
            "supplier": "Dairy Alternatives Inc.",
        },
    ]


def test_get_reorder_suggestions_filters_by_requested_skus() -> None:
    tool = make_get_reorder_suggestions()

    result = tool.invoke({"store": "main", "skus": ["SKU-142"]})

    assert [item["sku"] for item in result] == ["SKU-142"]


# --- fail_on injection (docs/TESTING.md §2) ---------------------------------


def test_fail_on_raises_a_clear_error_for_check_stockout() -> None:
    tool = make_check_stockout(fail_on=lambda args: args.store == "broken")

    with pytest.raises(RuntimeError, match=r"check_stockout failed.*broken"):
        tool.invoke({"store": "broken"})


def test_fail_on_only_triggers_when_the_predicate_matches() -> None:
    tool = make_check_stockout(fail_on=lambda args: args.store == "broken")

    assert tool.invoke({"store": "main"}) != []  # unaffected


def test_fail_on_works_for_get_reorder_suggestions() -> None:
    tool = make_get_reorder_suggestions(fail_on=lambda args: args.store == "broken")

    with pytest.raises(RuntimeError, match="get_reorder_suggestions failed"):
        tool.invoke({"store": "broken"})


def test_fail_on_works_for_send_reorder_email() -> None:
    tool = make_send_reorder_email(
        MockEffects(), fail_on=lambda args: args.to == "blocked@example.com"
    )

    with pytest.raises(RuntimeError, match="send_reorder_email failed"):
        tool.invoke({"to": "blocked@example.com", "subject": "x", "body": "y"})


# --- SEND_MODE double gate — T3's explicit completion criterion -------------


def test_dry_run_does_not_reach_the_real_send_path() -> None:
    mock = MockEffects(send_mode=SendMode.DRY_RUN)
    tool = make_send_reorder_email(mock)

    tool.invoke({"to": "ops@example.com", "subject": "Reorder", "body": "please reorder"})

    assert len(mock.send_email_calls) == 1  # the tool ran
    assert mock.live_send_calls == []  # but never reached the live path


def test_live_mode_does_reach_the_real_send_path() -> None:
    mock = MockEffects(send_mode=SendMode.LIVE)
    tool = make_send_reorder_email(mock)

    tool.invoke({"to": "ops@example.com", "subject": "Reorder", "body": "please reorder"})

    assert len(mock.live_send_calls) == 1


# --- ToolSpec wiring ---------------------------------------------------------


def test_build_builtin_tool_specs_wires_safe_effect_correctly() -> None:
    specs = build_builtin_tool_specs(MockEffects())

    by_name = {spec.tool.name: spec.requires_approval for spec in specs}

    assert by_name == {
        "check_stockout": False,
        "get_reorder_suggestions": False,
        "send_reorder_email": True,
    }


def test_check_stockout_args_can_be_constructed_directly() -> None:
    # sanity check that the exported schema is usable outside the tool too
    args = CheckStockoutArgs(store="main")
    assert args.days_ahead == 7


def test_get_reorder_suggestions_args_defaults_skus_to_none() -> None:
    args = GetReorderSuggestionsArgs(store="main")
    assert args.skus is None
