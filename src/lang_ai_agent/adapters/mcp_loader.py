"""MCP tool loader (docs/DESIGN.md §6): `mcp_servers.json` -> `ToolSpec`s.

Three layers, so the first two are testable without ever spawning a process
(docs/TESTING.md §4 — real connections are for `make smoke` only):

1. `parse_mcp_servers_config` / `load_mcp_servers_config` — the JSON config
   parsed at the boundary with Pydantic (`extra="forbid"`, so a typo like
   `"aproval"` fails loudly instead of silently making every tool effect).
2. `ApprovalPolicy.requires_approval` / `map_tools_to_specs` — the pure
   safe/effect mapping. A tool the config doesn't mention gets the
   server's `default`, which itself defaults to `"effect"`: approval
   required is the safe default when we don't know better (DESIGN §4).
3. `load_mcp_tool_specs` — the only layer that talks to servers, via an
   injectable client factory so tests hand in a fake.

v0.1 supports the `stdio` transport only (DESIGN §6); the other transports
`MultiServerMCPClient` knows are a v0.2 extension.
"""

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from lang_ai_agent.core.tools_spec import ToolSpec, merge_tool_specs

EXAMPLE_FILE = "mcp_servers.json.example"


class McpConfigError(ValueError):
    """`mcp_servers.json` is missing or invalid — the message says how to fix it."""


class ApprovalPolicy(BaseModel):
    """Per-server safe/effect classification (DESIGN §4, §6)."""

    model_config = ConfigDict(extra="forbid")

    default: Literal["safe", "effect"] = "effect"
    safe: list[str] = Field(default_factory=list)
    effect: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_tool_in_both_lists(self) -> "ApprovalPolicy":
        both = sorted(set(self.safe) & set(self.effect))
        if both:
            raise ValueError(
                f"tool(s) listed as both safe and effect: {both} — keep each in one list"
            )
        return self

    def requires_approval(self, tool_name: str) -> bool:
        if tool_name in self.safe:
            return False
        if tool_name in self.effect:
            return True
        return self.default == "effect"


class McpServerConfig(BaseModel):
    """One entry of `mcp_servers.json`, keyed by server name."""

    model_config = ConfigDict(extra="forbid")

    transport: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    approval: ApprovalPolicy = Field(default_factory=ApprovalPolicy)

    def connection(self) -> dict[str, Any]:
        """The connection dict `MultiServerMCPClient` expects for this server."""
        connection: dict[str, Any] = {
            "transport": self.transport,
            "command": self.command,
            "args": list(self.args),
        }
        if self.env:
            connection["env"] = dict(self.env)
        return connection


type McpServersConfig = dict[str, McpServerConfig]

_CONFIG_ADAPTER: TypeAdapter[dict[str, McpServerConfig]] = TypeAdapter(dict[str, McpServerConfig])


def parse_mcp_servers_config(text: str, *, source: str = "mcp_servers.json") -> McpServersConfig:
    """Parse the JSON text of an `mcp_servers.json`.

    Raises `McpConfigError` naming both the problem and the fix.
    """
    try:
        raw: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise McpConfigError(
            f"{source} is not valid JSON ({exc}). Fix the JSON — {EXAMPLE_FILE} is a "
            "known-good starting point."
        ) from exc
    if not isinstance(raw, dict) or not raw:
        raise McpConfigError(
            f"{source} must be a non-empty JSON object mapping server names to server "
            f"configs (see {EXAMPLE_FILE})."
        )
    try:
        return _CONFIG_ADAPTER.validate_python(raw)
    except ValidationError as exc:
        raise McpConfigError(
            f"{source} has an invalid server config:\n{exc}\nCompare it with {EXAMPLE_FILE}."
        ) from exc


def load_mcp_servers_config(path: Path) -> McpServersConfig:
    """Read and parse `mcp_servers.json` from `path`."""
    if not path.is_file():
        raise McpConfigError(
            f"{path} not found. Copy {EXAMPLE_FILE} to {path.name} and fill in each "
            "server's command/args (and its approval lists)."
        )
    return parse_mcp_servers_config(path.read_text(encoding="utf-8"), source=str(path))


def map_tools_to_specs(tools: Sequence[BaseTool], policy: ApprovalPolicy) -> list[ToolSpec]:
    """Wrap one server's loaded tools in ToolSpecs per its approval policy."""
    return [
        ToolSpec(tool=tool, requires_approval=policy.requires_approval(tool.name)) for tool in tools
    ]


class ToolClient(Protocol):
    """What the loader needs from `MultiServerMCPClient` — structurally, so a
    test can hand in a fake that never spawns anything.
    """

    async def get_tools(  # pragma: no cover - protocol stub, never executed
        self, *, server_name: str | None = None
    ) -> list[BaseTool]: ...


type ClientFactory = Callable[[dict[str, Any]], ToolClient]


def _real_client(connections: dict[str, Any]) -> ToolClient:  # pragma: no cover - spawns processes
    return MultiServerMCPClient(connections)


async def load_mcp_tool_specs(
    config: McpServersConfig, *, client_factory: ClientFactory = _real_client
) -> list[ToolSpec]:
    """Connect to every configured server and return its tools as ToolSpecs.

    Each server's tools are fetched with `get_tools(server_name=...)` so the
    right approval policy applies; `merge_tool_specs` then refuses a tool
    name that two servers both expose.
    """
    client = client_factory({name: server.connection() for name, server in config.items()})
    groups = [
        map_tools_to_specs(await client.get_tools(server_name=name), server.approval)
        for name, server in config.items()
    ]
    return merge_tool_specs(*groups)
