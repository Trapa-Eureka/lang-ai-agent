"""`lang-ai-agent` console script (docs/DESIGN.md §8.1, §10).

- `init`  — onboarding: provider → API key → model → bearer token → `.env`.
- `serve` — run the HTTP API without the Makefile; config problems are one
  message and exit code 2, never a traceback.
- `smoke` — the real-model smoke (`lang_ai_agent.smoke`), human-only.

The interactive parts take their console functions as parameters so the
whole flow runs in tests without a TTY; `main()` wires the real ones.
"""

import argparse
import getpass
import os
import secrets
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from lang_ai_agent.adapters.llm import PROVIDERS, Provider

ENV_PATH = Path(".env")


@dataclass(frozen=True)
class Console:
    """The three ways `init` talks to a human — swapped for fakes in tests."""

    ask: Callable[[str], str]
    ask_secret: Callable[[str], str]
    """Like `ask`, but the answer must not echo (API keys)."""
    say: Callable[[str], None]


def _real_console() -> Console:
    return Console(ask=input, ask_secret=getpass.getpass, say=print)


def render_env(provider: Provider, api_key: str, model: str, bearer_token: str) -> str:
    """The `.env` that `init` writes — the `.env.example` contract with the
    blanks filled in. SEND_MODE stays dry_run: going live is a deliberate
    manual edit (CLAUDE.md guardrail 3), never an onboarding default.
    """
    return (
        f"MODEL={model}\n"
        f"{provider.key_env}={api_key}\n"
        f"APP_BEARER_TOKEN={bearer_token}\n"
        "CHECKPOINT_DB_PATH=./data/checkpoints.db\n"
        "SEND_MODE=dry_run\n"
        "LANGSMITH_TRACING=false\n"
        "MCP_SERVERS_PATH=\n"
    )


def choose_provider(console: Console) -> Provider:
    console.say("Which model provider will this agent use?")
    for number, provider in enumerate(PROVIDERS, start=1):
        console.say(f"  {number}. {provider.label}")
    while True:
        answer = console.ask("Provider [1]: ").strip() or "1"
        if answer.isdigit() and 1 <= int(answer) <= len(PROVIDERS):
            return PROVIDERS[int(answer) - 1]
        console.say(f"Please enter a number from 1 to {len(PROVIDERS)}.")


def ask_api_key(console: Console, provider: Provider) -> str:
    while True:
        key = console.ask_secret(f"{provider.key_env} (input hidden): ").strip()
        if key:
            return key
        console.say("The key can't be empty — paste the key from your provider's console.")


def ask_model(console: Console, provider: Provider) -> str:
    """A bare model name gets the provider's prefix; an explicit
    `provider:model` answer is kept as typed.
    """
    answer = console.ask(f"Model [{provider.suggested_model}]: ").strip()
    model = answer or provider.suggested_model
    return model if ":" in model else f"{provider.id}:{model}"


def _write_private(path: Path, content: str) -> None:
    """Write `content` to `path` atomically, readable by the owner only.

    A temp file in the same directory (`mkstemp` creates it with mode 0600,
    so the key is never on disk world-readable), fsync, then `os.replace`:
    a crash mid-write leaves either the old file or the complete new one,
    never a truncated `.env` (audit 001, AUD-009).
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def run_init(path: Path, *, force: bool, console: Console) -> int:
    """Write `path` (normally `.env`) from an interactive session. Returns
    the process exit code: 1 when refusing to overwrite an existing file or
    to write through a symbolic link.
    """
    if path.is_symlink():
        console.say(
            f"{path} is a symbolic link — refusing to write through it. Remove the link, "
            "or pass a real file path with --path."
        )
        return 1
    if path.exists() and not force:
        console.say(
            f"{path} already exists — not overwriting. Re-run with --force to replace it, "
            "or edit it directly."
        )
        return 1
    provider = choose_provider(console)
    api_key = ask_api_key(console, provider)
    model = ask_model(console, provider)
    bearer_token = secrets.token_urlsafe(32)
    _write_private(path, render_env(provider, api_key, model, bearer_token))
    console.say(f"Wrote {path} (mode 0600) for MODEL={model}.")
    console.say(f"APP_BEARER_TOKEN={bearer_token}")
    console.say("Clients send it as `Authorization: Bearer <token>`. Next: `lang-ai-agent serve`.")
    return 0


def run_serve(host: str, port: int, *, err: Callable[[str], None]) -> int:
    """Start the API. A `ConfigError` (missing key/token) is printed through
    `err` as one message with the fix — exit code 2, no traceback.
    """
    # Imported here so `lang-ai-agent init` stays instant: the app pulls in
    # FastAPI + LangGraph + every provider SDK, which `init` never needs.
    import uvicorn

    from lang_ai_agent.api.app import ConfigError, create_default_app

    try:
        app = create_default_app()
    except ConfigError as e:
        err(f"lang-ai-agent serve: {e}")
        return 2
    uvicorn.run(app, host=host, port=port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lang-ai-agent",
        description="LangGraph agent backend with a human-approval gate for effectful tools.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser(
        "init", help="write .env interactively: provider, API key, model, bearer token"
    )
    init.add_argument("--force", action="store_true", help="overwrite an existing .env")
    init.add_argument("--path", type=Path, default=ENV_PATH, help="where to write (default: .env)")

    serve = commands.add_parser(
        "serve", help="run the HTTP API (reads .env; fails fast on missing configuration)"
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    smoke = commands.add_parser(
        "smoke",
        help="real-model smoke: scenario 1 + scenario 2 with console approval (spends API calls)",
    )
    smoke.add_argument(
        "--mcp", action="store_true", help="also load the MCP servers from mcp_servers.json"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init":
        return run_init(args.path, force=args.force, console=_real_console())
    if args.command == "serve":
        return run_serve(args.host, args.port, err=lambda line: print(line, file=sys.stderr))
    from lang_ai_agent.smoke import run_smoke  # same laziness as `serve`

    return run_smoke(mcp=args.mcp)


if __name__ == "__main__":
    sys.exit(main())
