"""Real-model smoke (docs/TESTING.md §5) — the one place a real model runs.

`run_scenarios` drives a compiled graph through SPEC §4 scenario 1 (query)
and scenario 2 (effect → interrupt → console approval) over the same SSE
mapper the API uses, so the smoke exercises the production path. It is a
function of its console callbacks, which is how the tests run it with a
scripted model and MockEffects (zero network); `run_smoke()` wires the real
settings and model and is human-only (`make smoke`, `lang-ai-agent smoke`
— it spends API calls).
"""

import asyncio
import sys
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from lang_ai_agent.adapters.checkpoint import thread_config
from lang_ai_agent.adapters.effects import SendMode
from lang_ai_agent.api.app import AgentGraph, ConfigError, load_settings, open_default_graph
from lang_ai_agent.api.sse import (
    ErrorEvent,
    InterruptEvent,
    TokenEvent,
    ToolEndEvent,
    ToolStartEvent,
    UsageEvent,
    stream_sse_events,
)
from lang_ai_agent.core.state import AgentState, Usage

SCENARIOS: tuple[str, ...] = (
    "본점(store id: main)에서 다음 주에 떨어질 품목이 뭐야? 표로 요약해줘.",
    "위험 품목 재주문 메일을 ops@example.com으로 보내줘.",
)
"""SPEC §4 scenarios 1 and 2 — the second must reach the approval gate."""

APPROVAL_PROMPT = "Approve? [y/N] "


async def run_scenarios(
    graph: AgentGraph,
    *,
    ask_yes_no: Callable[[str], bool],
    out: Callable[[str], None],
    prompts: Sequence[str] = SCENARIOS,
) -> int:
    """Run `prompts` in order on one fresh thread, answering every interrupt
    through `ask_yes_no`. `out` receives raw text (tokens stream inline).
    Returns a process exit code: 0 after the last `done`, 1 on any `error`.
    """
    config = thread_config(f"smoke-{uuid.uuid4()}")
    for prompt in prompts:
        out(f"\n> {prompt}\n")
        initial: AgentState = {
            "messages": [HumanMessage(content=prompt)],
            "pending": None,
            "usage": Usage(),
        }
        graph_input: AgentState | Command[Any] = initial
        while True:
            resume: Command[Any] | None = None
            async for event in stream_sse_events(graph, graph_input, config):
                if isinstance(event, TokenEvent):
                    out(event.content)
                elif isinstance(event, ToolStartEvent):
                    out(f"\n[tool] {event.tool_name} ...")
                elif isinstance(event, ToolEndEvent):
                    out(f" done ({event.duration_ms:.0f} ms)\n")
                elif isinstance(event, InterruptEvent):
                    pending = event.pending
                    out(f"\n[approval needed] {pending.tool_name} {pending.args_preview}\n")
                    # The graph always supplies a draft today (the body, or the
                    # args as JSON); the schema still allows None, hence the fallback.
                    out(f"--- draft ---\n{event.draft or '(none)'}\n-------------\n")
                    approved = ask_yes_no(APPROVAL_PROMPT)
                    comment = None if approved else "Rejected by the operator during the smoke."
                    resume = Command(resume={"approved": approved, "comment": comment})
                elif isinstance(event, UsageEvent):
                    usage = event.usage
                    out(
                        f"\n[usage] input={usage.input_tokens} output={usage.output_tokens} "
                        f"calls={usage.calls}\n"
                    )
                elif isinstance(event, ErrorEvent):
                    out(f"\n[error] {event.message}\n")
                    return 1
                # DoneEvent needs no line of its own: the usage line precedes it.
            if resume is None:
                break
            # The interrupt is always the stream's last event (api/sse.py), so
            # resuming here continues the same turn — and loops again if the
            # model comes back with a revised draft (SPEC §4-3).
            graph_input = resume
    return 0


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _console_yes_no(prompt: str) -> bool:
    return input(prompt).strip().lower() in {"y", "yes"}


def run_smoke(*, mcp: bool) -> int:
    """`lang-ai-agent smoke` / `make smoke`: real settings (`.env` from
    `lang-ai-agent init`) and real model. SEND_MODE is forced to dry_run —
    the smoke never sends for real (TESTING §5) — and MCP servers load only
    with `--mcp` (from MCP_SERVERS_PATH, else ./mcp_servers.json).
    """
    try:
        settings = load_settings()
    except ConfigError as e:
        _write(f"lang-ai-agent smoke: {e}\n")
        return 2
    settings = settings.model_copy(
        update={
            "send_mode": SendMode.DRY_RUN,
            "mcp_servers_path": (settings.mcp_servers_path or "mcp_servers.json") if mcp else None,
        }
    )

    async def _run() -> int:
        async with open_default_graph(settings) as graph:
            return await run_scenarios(
                graph, ask_yes_no=_console_yes_no, out=_write, prompts=SCENARIOS
            )

    return asyncio.run(_run())
