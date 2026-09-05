"""MCP loader tests (T9 completion criteria): config parsing and the
safe/effect mapping, with no real server process anywhere (docs/TESTING.md
§4 — a fake client stands in for MultiServerMCPClient).
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.tools import BaseTool, tool

from lang_ai_agent.adapters.mcp_loader import (
    EXAMPLE_FILE,
    ApprovalPolicy,
    McpConfigError,
    McpServerConfig,
    load_mcp_servers_config,
    load_mcp_tool_specs,
    map_tools_to_specs,
    parse_mcp_servers_config,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

_RETAIL = {
    "command": "npx",
    "args": ["tsx", "/path/to/retail-mcp/src/server.ts"],
    "transport": "stdio",
    "approval": {"default": "effect", "safe": ["stockout_risk", "inventory_status"]},
}


@tool
def stockout_risk(store: str) -> str:
    """fake MCP tool"""
    return store


@tool
def place_order(sku: str) -> str:
    """fake MCP tool"""
    return sku


# --- parsing ------------------------------------------------------------------


def test_the_committed_example_file_parses() -> None:
    """Keeps mcp_servers.json.example and the parser from drifting apart."""
    config = load_mcp_servers_config(_REPO_ROOT / EXAMPLE_FILE)

    assert set(config) == {"retail"}
    assert config["retail"].command == "npx"
    assert config["retail"].approval.default == "effect"
    assert "stockout_risk" in config["retail"].approval.safe


def test_parse_applies_defaults_for_omitted_fields() -> None:
    config = parse_mcp_servers_config(json.dumps({"minimal": {"command": "server-bin"}}))

    server = config["minimal"]
    assert server.transport == "stdio"
    assert server.args == []
    assert server.env is None
    assert server.approval == ApprovalPolicy()  # default: everything requires approval


def test_missing_file_error_says_how_to_fix_it(tmp_path: Path) -> None:
    with pytest.raises(McpConfigError, match=rf"not found.*Copy {EXAMPLE_FILE}"):
        load_mcp_servers_config(tmp_path / "mcp_servers.json")


def test_invalid_json_error_names_the_problem_and_the_example() -> None:
    with pytest.raises(McpConfigError, match=rf"not valid JSON.*{EXAMPLE_FILE}"):
        parse_mcp_servers_config("{not json")


@pytest.mark.parametrize("raw", ["[]", "{}", '"retail"'])
def test_root_must_be_a_non_empty_object(raw: str) -> None:
    with pytest.raises(McpConfigError, match="non-empty JSON object"):
        parse_mcp_servers_config(raw)


def test_a_typo_in_a_server_config_is_refused_not_silently_ignored() -> None:
    """`extra="forbid"`: an `aproval` typo must not quietly turn into
    "no approval config -> everything is effect" without anyone noticing."""
    with pytest.raises(McpConfigError, match="invalid server config"):
        parse_mcp_servers_config(json.dumps({"retail": {**_RETAIL, "aproval": {}}}))


def test_unsupported_transport_is_refused() -> None:
    with pytest.raises(McpConfigError, match="invalid server config"):
        parse_mcp_servers_config(json.dumps({"remote": {**_RETAIL, "transport": "sse"}}))


def test_a_tool_in_both_safe_and_effect_lists_is_ambiguous() -> None:
    with pytest.raises(McpConfigError, match="both safe and effect"):
        parse_mcp_servers_config(
            json.dumps({"retail": {**_RETAIL, "approval": {"safe": ["x"], "effect": ["x"]}}})
        )


def test_connection_dict_matches_what_multiserver_client_expects() -> None:
    server = McpServerConfig.model_validate({**_RETAIL, "env": {"RETAIL_TOKEN": "t"}})

    assert server.connection() == {
        "transport": "stdio",
        "command": "npx",
        "args": ["tsx", "/path/to/retail-mcp/src/server.ts"],
        "env": {"RETAIL_TOKEN": "t"},
    }
    assert "env" not in McpServerConfig.model_validate(_RETAIL).connection()


# --- approval mapping (DESIGN §4: unmentioned tools default to effect) ------


@pytest.mark.parametrize(
    ("policy", "tool_name", "expected"),
    [
        (ApprovalPolicy(), "anything", True),  # no config at all -> effect
        (ApprovalPolicy(default="effect", safe=["lookup"]), "lookup", False),
        (ApprovalPolicy(default="effect", safe=["lookup"]), "unlisted", True),
        (ApprovalPolicy(default="safe"), "unlisted", False),
        (ApprovalPolicy(default="safe", effect=["send"]), "send", True),
    ],
)
def test_requires_approval_mapping(policy: ApprovalPolicy, tool_name: str, expected: bool) -> None:
    assert policy.requires_approval(tool_name) is expected


def test_map_tools_to_specs_applies_the_policy_per_tool() -> None:
    specs = map_tools_to_specs(
        [stockout_risk, place_order], ApprovalPolicy(default="effect", safe=["stockout_risk"])
    )

    assert {s.tool.name: s.requires_approval for s in specs} == {
        "stockout_risk": False,
        "place_order": True,
    }


# --- load_mcp_tool_specs with a fake client (no process) ----------------------


class _FakeClient:
    def __init__(self, tools_by_server: dict[str, list[BaseTool]]) -> None:
        self.tools_by_server = tools_by_server
        self.requested: list[str | None] = []

    async def get_tools(self, *, server_name: str | None = None) -> list[BaseTool]:
        self.requested.append(server_name)
        assert server_name is not None
        return self.tools_by_server[server_name]


async def test_load_mcp_tool_specs_builds_connections_and_maps_per_server() -> None:
    seen_connections: dict[str, Any] = {}
    client = _FakeClient({"retail": [stockout_risk, place_order]})

    def factory(connections: dict[str, Any]) -> _FakeClient:
        seen_connections.update(connections)
        return client

    config = parse_mcp_servers_config(json.dumps({"retail": _RETAIL}))
    specs = await load_mcp_tool_specs(config, client_factory=factory)

    assert seen_connections == {"retail": config["retail"].connection()}
    assert client.requested == ["retail"]  # fetched per server, so the right policy applies
    assert {s.tool.name: s.requires_approval for s in specs} == {
        "stockout_risk": False,
        "place_order": True,
    }


async def test_two_servers_exposing_the_same_tool_name_are_refused() -> None:
    client = _FakeClient({"a": [stockout_risk], "b": [stockout_risk]})
    config = parse_mcp_servers_config(
        json.dumps({"a": {"command": "a-bin"}, "b": {"command": "b-bin"}})
    )

    with pytest.raises(ValueError, match="Duplicate tool name"):
        await load_mcp_tool_specs(config, client_factory=lambda _c: client)


# --- startup timeout / server failure (audit 001) -----------------------------


class _HangingClient:
    async def get_tools(self, *, server_name: str | None = None) -> list[BaseTool]:
        await asyncio.sleep(10)
        return []  # pragma: no cover - the timeout fires long before this


class _BrokenClient:
    async def get_tools(self, *, server_name: str | None = None) -> list[BaseTool]:
        raise RuntimeError(
            "spawn failed: /usr/local/bin/npx: No such file\n  at Object.<anonymous>"
        )


async def test_a_server_that_never_answers_fails_startup_with_a_timeout_message() -> None:
    config = parse_mcp_servers_config(json.dumps({"retail": _RETAIL}))

    with pytest.raises(McpConfigError, match=r"'retail' did not return its tools within 0.01s"):
        await load_mcp_tool_specs(
            config, client_factory=lambda _c: _HangingClient(), startup_timeout_s=0.01
        )


async def test_a_server_that_fails_to_start_names_it_and_keeps_the_first_line_only() -> None:
    config = parse_mcp_servers_config(json.dumps({"retail": _RETAIL}))

    with pytest.raises(McpConfigError, match=r"'retail' failed to start") as exc_info:
        await load_mcp_tool_specs(config, client_factory=lambda _c: _BrokenClient())

    message = str(exc_info.value)
    assert "RuntimeError: spawn failed" in message
    assert "at Object" not in message  # describe_error: first line only
    assert "mcp_servers.json" in message  # and the fix
