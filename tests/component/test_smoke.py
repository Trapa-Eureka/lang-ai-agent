"""The real-model smoke's console loop (docs/TESTING.md §4 "온보딩·설정", §5),
driven here by a scripted model and MockEffects — the exact flow a human runs
with `make smoke`, verified with zero network calls (guardrail 2)."""

from pathlib import Path
from typing import Any

import pytest

import lang_ai_agent.smoke as smoke_module
from lang_ai_agent.adapters.effects import SendMode
from lang_ai_agent.api.app import Settings
from lang_ai_agent.smoke import APPROVAL_PROMPT, run_scenarios, run_smoke
from tests.component.conftest import GraphHarness, MakeHarness
from tests.helpers.scripted_chat_model import script

_QUERY_THEN_EMAIL = (
    script()
    .tool_call("check_stockout", {"store": "main"})
    .final("Two items are at risk.")
    .tool_call(
        "send_reorder_email", {"to": "ops@example.com", "subject": "Reorder", "body": "35 units"}
    )
    .final("Sent the reorder email.")
    .build()
)


def _always(answer: bool) -> Any:
    def ask(prompt: str) -> bool:
        return answer

    return ask


async def test_run_scenarios_streams_both_scenarios_and_approves_via_the_console(
    make_harness: MakeHarness,
) -> None:
    harness: GraphHarness = make_harness(_QUERY_THEN_EMAIL)
    asked: list[str] = []
    out: list[str] = []

    def yes(prompt: str) -> bool:
        asked.append(prompt)
        return True

    code = await run_scenarios(
        harness.graph,
        ask_yes_no=yes,
        out=out.append,
        prompts=("what's at risk?", "send the reorder email"),
    )

    text = "".join(out)
    assert code == 0
    assert asked == [APPROVAL_PROMPT]  # exactly one approval, for the effect tool
    assert "[tool] check_stockout" in text
    assert "Two items are at risk." in text
    # the operator sees what they're approving before being asked
    assert text.index("[approval needed] send_reorder_email") < text.index("Sent the reorder")
    assert "35 units" in text  # the draft
    assert text.count("[usage]") == 2
    assert len(harness.effects.send_email_calls) == 1  # approved -> ran (dry_run in MockEffects)
    harness.model.assert_exhausted()


async def test_run_scenarios_rejection_skips_the_effect(make_harness: MakeHarness) -> None:
    # No `body` -> the draft falls back to the args as JSON (core/graph.py);
    # the operator still sees what they're rejecting, and the tool never
    # runs, so its argument validation never gets a say.
    harness: GraphHarness = make_harness(
        script()
        .tool_call("send_reorder_email", {"to": "ops@example.com", "subject": "R"})
        .final("Understood, not sending.")
        .build()
    )
    out: list[str] = []

    code = await run_scenarios(
        harness.graph, ask_yes_no=_always(False), out=out.append, prompts=("send it",)
    )

    text = "".join(out)
    assert code == 0
    assert harness.effects.send_email_calls == []
    assert "[approval needed] send_reorder_email" in text
    assert "--- draft ---" in text and "ops@example.com" in text  # JSON fallback draft
    assert "Understood, not sending." in text


@pytest.mark.parametrize(
    ("typed", "expected"), [("y", True), ("Y", True), ("yes", True), ("", False), ("n", False)]
)
def test_console_yes_no_only_accepts_an_explicit_yes(
    monkeypatch: pytest.MonkeyPatch, typed: str, expected: bool
) -> None:
    def fake_input(prompt: str = "") -> str:
        return typed

    monkeypatch.setattr("builtins.input", fake_input)

    assert smoke_module._console_yes_no(APPROVAL_PROMPT) is expected  # pyright: ignore[reportPrivateUsage] - the real console binding is what's under test


async def test_run_scenarios_reports_a_model_error_as_exit_code_1(
    make_harness: MakeHarness,
) -> None:
    harness: GraphHarness = make_harness([])  # empty script -> the first call errors
    out: list[str] = []

    code = await run_scenarios(
        harness.graph, ask_yes_no=_always(True), out=out.append, prompts=("hi",)
    )

    assert code == 1
    assert "[error]" in "".join(out)


@pytest.mark.parametrize("mcp", [False, True])
def test_run_smoke_forces_dry_run_and_loads_mcp_only_on_request(
    make_harness: MakeHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mcp: bool
) -> None:
    """`run_smoke` is the human entry: real Settings, but SEND_MODE forced to
    dry_run whatever .env says, and MCP only with --mcp. The graph itself is
    swapped for a scripted one here (sync test: run_smoke owns the loop).
    """
    harness: GraphHarness = make_harness(script().final("hello").build())
    seen: list[Settings] = []

    class FakeOpen:
        def __init__(self, settings: Settings) -> None:
            seen.append(settings)

        async def __aenter__(self) -> Any:
            return harness.graph

        async def __aexit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(smoke_module, "open_default_graph", FakeOpen)
    monkeypatch.setattr(smoke_module, "SCENARIOS", ("hi",))
    monkeypatch.chdir(tmp_path)  # no .env here; everything comes from the env
    monkeypatch.setenv("APP_BEARER_TOKEN", "t")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("SEND_MODE", "live")
    monkeypatch.delenv("MCP_SERVERS_PATH", raising=False)

    code = run_smoke(mcp=mcp)

    assert code == 0
    assert len(seen) == 1
    assert seen[0].send_mode is SendMode.DRY_RUN
    assert seen[0].mcp_servers_path == ("mcp_servers.json" if mcp else None)
    harness.model.assert_exhausted()


def test_run_smoke_fails_fast_with_the_onboarding_fix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    for var in ("APP_BEARER_TOKEN", "ANTHROPIC_API_KEY", "MODEL"):
        monkeypatch.delenv(var, raising=False)

    code = run_smoke(mcp=False)

    assert code == 2
    printed = capsys.readouterr().out
    assert "APP_BEARER_TOKEN" in printed
    assert "lang-ai-agent init" in printed
