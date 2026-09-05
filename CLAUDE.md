# CLAUDE.md — lang_ai_agent steering

A LangGraph-based production agent backend (Ops Copilot demo). Spec in `docs/SPEC.md`, design in `docs/DESIGN.md`. **This repo is a public portfolio, so code quality is held to delivery standard.**

## Stack

- Python 3.12+, package management with **uv** (pyproject.toml is the single source)
- Types: **pyright strict** + Pydantic v2 — the Python reproduction of "static types are the agent's cheapest feedback loop"
- Agent: LangGraph (custom StateGraph — not using prebuilt is intentional), LangChain core
- Model: model-agnostic via `init_chat_model`, Claude by default (`langchain-anthropic`)
- MCP tools: `langchain-mcp-adapters` (`MultiServerMCPClient`)
- API: FastAPI + SSE streaming; checkpoints: InMemorySaver (tests) / AsyncSqliteSaver (dev and single server; the graph is async, so the synchronous SqliteSaver cannot be used)
- Verification: pytest + pytest-asyncio, ruff (lint + format), pyright

## Commands (Makefile)

```bash
make check        # ruff check + pyright + pytest — the required gate for task completion
make test         # pytest + core/ coverage gate ≥ 90% (SPEC §5)
make lint         # ruff check + format --check
make typecheck    # pyright
make dev          # uvicorn dev server
make smoke        # manual smoke with a real model and real MCP (humans only, = lang-ai-agent smoke)
uv run lang-ai-agent init    # onboarding: choose a provider + API key → .env (for installers, DESIGN §8.1)
uv run lang-ai-agent serve   # start from .env — a missing key or token is a ConfigError at startup
```

## Source layout

```
src/lang_ai_agent/
  core/        # state.py (AgentState, Pydantic models), graph.py (StateGraph), tools_spec.py (tool classification convention)
  adapters/    # llm.py (model factory, provider table PROVIDERS), checkpoint.py, mcp_loader.py, effects.py (side effects such as sending)
  api/         # app.py (FastAPI assembly, Settings, ConfigError), sse.py (event mapping, content_text), auth.py
  cli.py       # console script lang-ai-agent: init (onboarding) / serve / smoke
  smoke.py     # real-model smoke logic — the console loop is tested with a scripted model
tests/         # helpers/ (ScriptedChatModel, fake tools, fixed clock), unit, component, e2e-mock
scripts/       # smoke.py — humans only (thin wrapper around lang_ai_agent.smoke)
.github/workflows/ci.yml   # make check on every push and PR
mcp_servers.json.example   # example MCP tool connection configuration
```

## Conventions

- Under pyright strict, `# type: ignore` is forbidden without a reason comment. No functions returning `Any`; boundaries (requests, model output, MCP responses) are parsed with Pydantic.
- Async by default. Graph nodes stay thin — pull logic into pure functions so it is unit-testable.
- **No large payloads in State** — state is serialized at every checkpoint, so it holds messages and minimal metadata only; large results are summarized before they enter it.
- Error messages include the cause and the fix (e.g. `mcp_servers.json not found. Copy mcp_servers.json.example and fill in the server paths.`). Configuration problems surface **at startup** as `ConfigError`, not on the first request.
- Model `content` is either `str` **or** a list of content blocks (real models send the list) — read text through `content_text()` in `api/sse.py`. Checking only `isinstance(content, str)` silently yields an empty string with a real model.
- Exception strings visible to clients (SSE `error`) and to the model (error ToolMessage) come only from `core/errors.describe_error()` (type + first line, 200 chars). The full traceback goes to the server log only (`exc_info`).
- Graph runs on the same thread_id are serialized by `api/thread_locks.py` — any new endpoint that changes thread state must run inside that lock (DESIGN §5 note).
- Commit messages: `T{n}: Summary`, **in English**. **Everything in the repo is English** — docs, comments, docstrings, user-facing strings, test fixtures (the repo is a portfolio for the global contracting market and the package is public; switched from Korean docs on 2026-09-05).

## Guardrails (never violate)

1. **No execution path for a side-effecting tool (requires_approval=True) may exist in the code that skips the approval interrupt.** Enforced by a graph-structure test — no workarounds.
2. **Zero network and real-LLM calls in tests.** Model = ScriptedChatModel, MCP = fake tools, sending = mock. Real models only in `make smoke` and evals.
3. Side effects such as sending are double-gated: the graph's approval interrupt **and** `SEND_MODE=live`. Tests always take the dry_run path.
4. Secrets (`ANTHROPIC_API_KEY`, `APP_BEARER_TOKEN`, etc.) live only in `.env`. Never commit them; only `.env.example` is committed.
5. If a ScriptedChatModel script is exhausted or diverges from expectations, **fail loudly instead of passing silently** (block the seed of flakiness).
6. When spec/design and code conflict, fix `docs/` first. In particular, a graph topology change is preceded by a DESIGN §3 edit.

## Way of working

- One session = one task from `docs/TASKS.md`. Self-correct until every completion criterion is met and `make check` passes. Stop and ask only when blocked by spec ambiguity.
- On completion, summarize the changed files and verification results, then stop.

## Pruning log

Reviewed biweekly; stale rules deleted (`docs/WORKFLOW.md`).

- 2026-09-04: first version.
- 2026-09-04: commit message language fixed to English (prompted by correcting the T12 commit message).
- 2026-09-05: onboarding CLI (`lang-ai-agent init/serve/smoke`), provider table and content_text rule added (prompted by the T11 real-model smoke).
- 2026-09-05: error sanitization (`describe_error`) and thread serialization rules added (code audit 001, T16).
- 2026-09-05: whole repo switched to English — docs, comments, strings (T17); the "docs stay Korean" rule removed.
