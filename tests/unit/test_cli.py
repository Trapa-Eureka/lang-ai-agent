"""The `lang-ai-agent` console script (T11 — docs/DESIGN.md §8.1 onboarding,
§10 console script; docs/TESTING.md §4 "온보딩·설정")."""

import stat
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI

import lang_ai_agent.cli as cli
from lang_ai_agent.adapters.llm import PROVIDERS
from lang_ai_agent.api.app import ConfigError
from lang_ai_agent.cli import Console, main, render_env, run_init, run_serve

_REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeConsole:
    """Scripted answers for `init`; an unscripted prompt fails the test
    instead of hanging or answering something plausible (guardrail 5's
    spirit applied to the console).
    """

    def __init__(self, answers: list[str], secrets: list[str]) -> None:
        self._answers = iter(answers)
        self._secrets = iter(secrets)
        self.prompts: list[str] = []
        self.said: list[str] = []

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        answer = next(self._answers, None)
        assert answer is not None, f"init asked {prompt!r} but the fake console has no answer"
        return answer

    def ask_secret(self, prompt: str) -> str:
        self.prompts.append(prompt)
        secret = next(self._secrets, None)
        assert secret is not None, f"init asked {prompt!r} but the fake console has no secret"
        return secret

    def say(self, line: str) -> None:
        self.said.append(line)

    def as_console(self) -> Console:
        return Console(ask=self.ask, ask_secret=self.ask_secret, say=self.say)


def _value_of(text: str, key: str) -> str:
    return next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith(f"{key}="))


# --- init -------------------------------------------------------------------


def test_init_writes_a_private_env_file_from_the_answers(tmp_path: Path) -> None:
    openai = PROVIDERS[1]
    console = FakeConsole(answers=["2", ""], secrets=["sk-test-not-a-real-key"])
    path = tmp_path / ".env"

    code = run_init(path, force=False, console=console.as_console())

    assert code == 0
    text = path.read_text()
    assert _value_of(text, "MODEL") == f"openai:{openai.suggested_model}"
    assert _value_of(text, "OPENAI_API_KEY") == "sk-test-not-a-real-key"
    assert _value_of(text, "SEND_MODE") == "dry_run"  # never live from onboarding
    token = _value_of(text, "APP_BEARER_TOKEN")
    assert len(token) >= 32
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # the token is shown once so the operator can use it; the key never is
    assert any(token in line for line in console.said)
    assert not any("sk-test" in line for line in console.said)
    # the key was asked through the no-echo channel, not the plain one
    assert any("OPENAI_API_KEY" in prompt and "hidden" in prompt for prompt in console.prompts)


def test_init_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("KEEP=1\n")
    console = FakeConsole(answers=[], secrets=[])

    code = run_init(path, force=False, console=console.as_console())

    assert code == 1
    assert path.read_text() == "KEEP=1\n"
    assert any("--force" in line for line in console.said)
    assert console.prompts == []  # refused before asking for anything


def test_init_force_replaces_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("OLD=1\n")
    path.chmod(0o644)
    console = FakeConsole(answers=["1", ""], secrets=["sk-test"])

    code = run_init(path, force=True, console=console.as_console())

    assert code == 0
    assert "OLD=1" not in path.read_text()
    assert _value_of(path.read_text(), "ANTHROPIC_API_KEY") == "sk-test"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_init_reprompts_on_a_bad_provider_choice_and_an_empty_key(tmp_path: Path) -> None:
    console = FakeConsole(answers=["9", "x", "1", ""], secrets=["   ", "sk-test"])
    path = tmp_path / ".env"

    code = run_init(path, force=False, console=console.as_console())

    assert code == 0
    assert _value_of(path.read_text(), "ANTHROPIC_API_KEY") == "sk-test"
    assert sum("1 to 4" in line for line in console.said) == 2
    assert sum("can't be empty" in line for line in console.said) == 1


def test_init_prefixes_a_bare_model_name_and_keeps_an_explicit_one(tmp_path: Path) -> None:
    bare = FakeConsole(answers=["3", "grok-4-fast"], secrets=["xai-test"])
    explicit = FakeConsole(answers=["1", "anthropic:claude-opus-5"], secrets=["sk-test"])

    run_init(tmp_path / "bare.env", force=False, console=bare.as_console())
    run_init(tmp_path / "explicit.env", force=False, console=explicit.as_console())

    assert _value_of((tmp_path / "bare.env").read_text(), "MODEL") == "xai:grok-4-fast"
    assert _value_of((tmp_path / "bare.env").read_text(), "XAI_API_KEY") == "xai-test"
    assert _value_of((tmp_path / "explicit.env").read_text(), "MODEL") == "anthropic:claude-opus-5"


def test_init_refuses_to_write_through_a_symlink(tmp_path: Path) -> None:
    """AUD-009: `--force` must not follow a link and overwrite its target."""
    target = tmp_path / "elsewhere.txt"
    target.write_text("precious\n")
    link = tmp_path / ".env"
    link.symlink_to(target)
    console = FakeConsole(answers=[], secrets=[])

    code = run_init(link, force=True, console=console.as_console())

    assert code == 1
    assert target.read_text() == "precious\n"
    assert any("symbolic link" in line for line in console.said)
    assert console.prompts == []


def test_init_write_is_atomic_when_the_final_rename_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AUD-009: a failure mid-write leaves the old .env intact and no temp
    file behind — never a truncated or half-written file."""
    path = tmp_path / ".env"
    path.write_text("OLD=1\n")

    def disk_full(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(cli.os, "replace", disk_full)
    console = FakeConsole(answers=["1", ""], secrets=["sk-test"])

    with pytest.raises(OSError, match="disk full"):
        run_init(path, force=True, console=console.as_console())

    assert path.read_text() == "OLD=1\n"
    assert [p.name for p in tmp_path.iterdir()] == [".env"]


def test_render_env_stays_within_the_env_example_contract() -> None:
    """Every key `init` writes must be one `.env.example` documents — the
    example is the contract (DESIGN §8), `init` just fills it in.
    """
    example_keys = {
        line.split("=", 1)[0]
        for line in (_REPO_ROOT / ".env.example").read_text().splitlines()
        if line and not line.startswith("#")
    }
    rendered = render_env(PROVIDERS[0], "k", "anthropic:m", "t")
    rendered_keys = [line.split("=", 1)[0] for line in rendered.splitlines()]

    assert set(rendered_keys) <= example_keys
    assert {"MODEL", "ANTHROPIC_API_KEY", "APP_BEARER_TOKEN", "SEND_MODE"} <= set(rendered_keys)
    assert len(rendered_keys) == len(set(rendered_keys))


# --- serve ------------------------------------------------------------------


def test_serve_runs_uvicorn_with_the_default_app(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    import lang_ai_agent.api.app as app_module

    sentinel = FastAPI()
    calls: list[dict[str, object]] = []

    def fake_create_default_app() -> FastAPI:
        return sentinel

    def fake_run(app: object, **kwargs: object) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setattr(app_module, "create_default_app", fake_create_default_app)
    monkeypatch.setattr(uvicorn, "run", fake_run)
    errors: list[str] = []

    code = run_serve("0.0.0.0", 9000, err=errors.append)

    assert code == 0
    assert calls == [{"app": sentinel, "host": "0.0.0.0", "port": 9000}]
    assert errors == []


def test_serve_prints_a_config_error_as_one_message_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lang_ai_agent.api.app as app_module

    def boom() -> FastAPI:
        raise ConfigError("ANTHROPIC_API_KEY is not set.\nFix: run `lang-ai-agent init`.")

    monkeypatch.setattr(app_module, "create_default_app", boom)
    errors: list[str] = []

    code = run_serve("127.0.0.1", 8000, err=errors.append)

    assert code == 2
    assert errors == [
        "lang-ai-agent serve: ANTHROPIC_API_KEY is not set.\nFix: run `lang-ai-agent init`."
    ]


# --- main (argument parsing + wiring) ----------------------------------------


def test_main_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 2


def test_main_init_uses_the_terminal_and_hides_the_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = iter(["1", ""])

    def fake_input(prompt: str = "") -> str:
        return next(answers)

    def fake_getpass(prompt: str = "Password: ") -> str:
        return "sk-test-not-a-real-key"

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr("getpass.getpass", fake_getpass)
    path = tmp_path / ".env"

    code = main(["init", "--path", str(path)])

    assert code == 0
    assert _value_of(path.read_text(), "ANTHROPIC_API_KEY") == "sk-test-not-a-real-key"
    printed = capsys.readouterr().out
    assert "APP_BEARER_TOKEN=" in printed
    assert "sk-test" not in printed


def test_main_serve_passes_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, int]] = []

    def fake_serve(host: str, port: int, *, err: Callable[[str], None]) -> int:
        seen.append((host, port))
        return 0

    monkeypatch.setattr(cli, "run_serve", fake_serve)

    assert main(["serve", "--host", "0.0.0.0", "--port", "9001"]) == 0
    assert seen == [("0.0.0.0", 9001)]


def test_main_serve_reports_config_errors_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import lang_ai_agent.api.app as app_module

    def boom() -> FastAPI:
        raise ConfigError("APP_BEARER_TOKEN missing. Fix: run `lang-ai-agent init`.")

    monkeypatch.setattr(app_module, "create_default_app", boom)

    code = main(["serve"])

    captured = capsys.readouterr()
    assert code == 2
    assert "lang-ai-agent serve: APP_BEARER_TOKEN missing" in captured.err
    assert captured.out == ""


def test_main_smoke_passes_the_mcp_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    import lang_ai_agent.smoke as smoke_module

    seen: list[bool] = []

    def fake_smoke(*, mcp: bool) -> int:
        seen.append(mcp)
        return 7

    monkeypatch.setattr(smoke_module, "run_smoke", fake_smoke)

    assert main(["smoke", "--mcp"]) == 7
    assert main(["smoke"]) == 7
    assert seen == [True, False]
