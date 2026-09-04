"""Built-in v0.1 tools (docs/DESIGN.md §4, docs/SPEC.md §2 goal 4).

These are the entire tool layer for v0.1 — no real retail-mcp connection
exists yet (that's T9's optional MCP-loader path). All three are
deliberately fake/simulated: `check_stockout` and `get_reorder_suggestions`
return fixed, deterministic sample data; `send_reorder_email`'s "send" is
gated through adapters/effects.py rather than a real provider. The actual
retail-mcp repo (a sibling project, not part of this workspace) wasn't
available to copy an exact schema from, so these schemas are a plausible
approximation of what it might expose, not a literal mirror — worth
double-checking against the real thing if exact parity ever matters.

Each tool is built by a factory function rather than a bare `@tool`-decorated
module-level function, so tests can inject `fail_on` — a predicate that
turns a normal call into a deterministic error, to exercise the graph's
tool-error handling (docs/TESTING.md §2/§4) — and, for `send_reorder_email`,
an effects backend (real `Effects` or tests' `MockEffects`), all without any
shared global/mutable state.
"""

from collections.abc import Callable
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from lang_ai_agent.adapters.effects import SupportsSendEmail
from lang_ai_agent.core.tools_spec import ToolSpec


class CheckStockoutArgs(BaseModel):
    store: str = Field(description="Store identifier, e.g. 'main' or 'downtown'.")
    days_ahead: int = Field(default=7, ge=1, le=90, description="Lookahead window in days.")


class StockoutRiskItem(BaseModel):
    sku: str
    name: str
    days_until_stockout: int


class GetReorderSuggestionsArgs(BaseModel):
    store: str = Field(description="Store identifier.")
    skus: list[str] | None = Field(
        default=None,
        description="Specific SKUs to get suggestions for; omit for all at-risk items.",
    )


class ReorderSuggestion(BaseModel):
    sku: str
    name: str
    suggested_quantity: int
    supplier: str


class SendReorderEmailArgs(BaseModel):
    to: str = Field(description="Recipient email address.")
    subject: str = Field(description="Email subject line.")
    body: str = Field(description="Email body — the draft a human approves before it sends.")


# Fixed, deterministic sample data (docs/TESTING.md §2: fakes return a
# "고정 응답" — a fixed response — not randomized data).
_STOCKOUT_RISK: dict[str, list[StockoutRiskItem]] = {
    "main": [
        StockoutRiskItem(sku="SKU-100", name="Espresso Beans 1kg", days_until_stockout=3),
        StockoutRiskItem(sku="SKU-142", name="Oat Milk 1L", days_until_stockout=5),
    ],
    "downtown": [
        StockoutRiskItem(sku="SKU-200", name="Paper Cups 500ct", days_until_stockout=2),
    ],
}

_SUPPLIERS: dict[str, str] = {
    "SKU-100": "Roastworks Co.",
    "SKU-142": "Dairy Alternatives Inc.",
    "SKU-200": "Packrite Supply",
}


def make_check_stockout(*, fail_on: Callable[[CheckStockoutArgs], bool] | None = None) -> BaseTool:
    """Build the `check_stockout` tool (safe)."""

    def _run(store: str, days_ahead: int = 7) -> list[dict[str, Any]]:
        args = CheckStockoutArgs(store=store, days_ahead=days_ahead)
        if fail_on is not None and fail_on(args):
            raise RuntimeError(f"check_stockout failed for store={store!r} (injected failure)")
        items = _STOCKOUT_RISK.get(store, [])
        return [item.model_dump() for item in items if item.days_until_stockout <= days_ahead]

    return StructuredTool.from_function(
        func=_run,
        name="check_stockout",
        description=(
            "Check which items are at risk of stocking out at a store "
            "within the given lookahead window."
        ),
        args_schema=CheckStockoutArgs,
    )


def make_get_reorder_suggestions(
    *, fail_on: Callable[[GetReorderSuggestionsArgs], bool] | None = None
) -> BaseTool:
    """Build the `get_reorder_suggestions` tool (safe)."""

    def _run(store: str, skus: list[str] | None = None) -> list[dict[str, Any]]:
        args = GetReorderSuggestionsArgs(store=store, skus=skus)
        if fail_on is not None and fail_on(args):
            raise RuntimeError(
                f"get_reorder_suggestions failed for store={store!r} (injected failure)"
            )
        items = _STOCKOUT_RISK.get(store, [])
        if skus is not None:
            wanted = set(skus)
            items = [item for item in items if item.sku in wanted]
        return [
            ReorderSuggestion(
                sku=item.sku,
                name=item.name,
                suggested_quantity=max(1, 10 - item.days_until_stockout) * 5,
                supplier=_SUPPLIERS.get(item.sku, "Unknown Supplier"),
            ).model_dump()
            for item in items
        ]

    return StructuredTool.from_function(
        func=_run,
        name="get_reorder_suggestions",
        description="Suggest reorder quantities and suppliers for at-risk items at a store.",
        args_schema=GetReorderSuggestionsArgs,
    )


def make_send_reorder_email(
    effects: SupportsSendEmail,
    *,
    fail_on: Callable[[SendReorderEmailArgs], bool] | None = None,
) -> BaseTool:
    """Build the `send_reorder_email` tool (effect — requires approval).

    `effects` is injected so production wiring passes the real `Effects`
    and tests pass `MockEffects` (docs/TESTING.md §2); this factory itself
    knows nothing about `SEND_MODE`.
    """

    def _run(to: str, subject: str, body: str) -> str:
        args = SendReorderEmailArgs(to=to, subject=subject, body=body)
        if fail_on is not None and fail_on(args):
            raise RuntimeError(f"send_reorder_email failed for to={to!r} (injected failure)")
        result = effects.send_email(to=to, subject=subject, body=body)
        return result.detail

    return StructuredTool.from_function(
        func=_run,
        name="send_reorder_email",
        description="Send a reorder email. Requires human approval before it runs.",
        args_schema=SendReorderEmailArgs,
    )


def build_builtin_tool_specs(effects: SupportsSendEmail) -> list[ToolSpec]:
    """The v0.1 built-in tool set as ToolSpecs, per DESIGN §4's safe/effect split."""
    return [
        ToolSpec(tool=make_check_stockout(), requires_approval=False),
        ToolSpec(tool=make_get_reorder_suggestions(), requires_approval=False),
        ToolSpec(tool=make_send_reorder_email(effects), requires_approval=True),
    ]
