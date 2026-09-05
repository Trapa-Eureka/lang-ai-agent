"""Tool classification convention (see docs/DESIGN.md §4).

Every tool the graph can call is wrapped in a `ToolSpec` that says whether
it requires human approval before it runs:

- safe (requires_approval=False): read-only / side-effect-free. Runs freely
  in the `safe_tools` node.
- effect (requires_approval=True): has a real-world side effect (e.g.
  sending an email). Must pass through the `approval` interrupt node — see
  CLAUDE.md guardrail 1: no code path may reach an effect tool without
  going through approval first.

v0.1 built-in tools (T3) are fakes mirroring the retail-mcp schema:
`check_stockout` and `get_reorder_suggestions` are safe, `send_reorder_email`
is effect. Tools loaded from `mcp_servers.json` (T9) get their
`requires_approval` value from that config's per-server/per-tool `approval`
mapping; a tool missing from that mapping defaults to effect — approval
required is the safe default when we don't know better.
"""

from collections import Counter
from collections.abc import Sequence

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict


class ToolSpec(BaseModel):
    """Binds one tool instance to its approval requirement."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    tool: BaseTool
    requires_approval: bool


def merge_tool_specs(*groups: Sequence[ToolSpec]) -> list[ToolSpec]:
    """Concatenate tool sets (built-in + each MCP server), refusing duplicate
    tool names.

    The graph looks tools up by name (core/graph.py), so a duplicate would
    let a later spec silently shadow an earlier one — and with it, that
    tool's approval requirement. Failing loudly here is the safe choice.
    """
    merged = [spec for group in groups for spec in group]
    duplicates = sorted(name for name, n in Counter(s.tool.name for s in merged).items() if n > 1)
    if duplicates:
        raise ValueError(
            f"Duplicate tool name(s) across tool sources: {duplicates}. Each tool the "
            "graph can call needs a unique name — rename the tool on one side, or drop "
            "the server that re-exposes it (mcp_servers.json)."
        )
    return merged
