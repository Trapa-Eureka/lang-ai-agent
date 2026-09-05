# SPEC — lang_ai_agent v0.1

Written: 2026-09-04 · Status: final (change this document first when anything changes)
Revised: 2026-09-04 — added PyPI release as a v0.1 goal; npm (JS/TS client SDK) release deferred (T12)
Revised: 2026-09-05 — added self-service onboarding (goal 8); pulled GitHub Actions CI from v0.2 into v0.1 (T11)
Revised: 2026-09-05 — all documentation switched from Korean to English (T17)

## 1. Background

LangChain/LangGraph agent backends are currently the highest-demand, highest-rate item in the global remote contracting market. What decides a bid, however, is not "I have run an agent" but **whether the production patterns are in place**: streaming, durable state with restart resilience, human-in-the-loop approval, deterministic tests, observability. This repo builds a reference that has all of those patterns, so that it can

- serve as a public portfolio (for contract bids and technical vetting), and
- be reused as the **agent runtime layer** of the MCP automation core — the tools are our own MCP servers (retail-mcp and friends).

Demo domain: **Ops Copilot** — multi-store retail operations queries and reorder-email sending. The domain is only a demo; the structure is meant to be reused regardless of domain.

## 2. v0.1 goals

1. **Custom StateGraph agent**: LLM tool-calling loop where **side-effecting tools must pass through an `interrupt()` approval gate**. Approve / reject / revise, then resume with `Command`.
2. **Durability and restart resilience**: checkpointer-based. After a server restart the same thread_id resumes from the interrupt point.
3. **HTTP API (FastAPI)**: create thread → send message (SSE stream: tokens, tool events, interrupt) → read state → approve/reject to resume. Bearer token auth.
4. **Tool layer**: a classification convention of read-only (safe) vs side-effecting (effect, requires_approval). v0.1 tools are three fakes mirroring the retail-mcp schema (`check_stockout`, `get_reorder_suggestions`, `send_reorder_email`) plus an MCP loader (`mcp_servers.json` → `MultiServerMCPClient`) as the path to real MCP servers.
5. **Deterministic tests**: the whole graph trajectory is verified with a ScriptedChatModel, no real LLM (`docs/TESTING.md`).
6. **Minimal observability**: per-thread token/cost accounting, structured logs, optional LangSmith tracing (env on/off).
7. **PyPI release**: finalize `pyproject.toml` distribution metadata → verify on TestPyPI → publish an installable package to PyPI (DESIGN §10).
8. **Self-service onboarding**: whoever installs the package runs `lang-ai-agent init` to put their own provider API key (Anthropic default / OpenAI / xAI / Google) into `.env`, then `lang-ai-agent serve` to start. A missing key fails at startup, with the fix in the message, rather than on the first model call (DESIGN §8.1).

## 3. v0.1 non-goals

- Multi-agent (supervisor / subgraphs) — v0.2
- PostgresSaver and horizontally scaled deployment — v0.2 (v0.1 is SqliteSaver, single server)
- Evaluation harness (eval sets, LLM-as-judge, trajectory regression) — v0.3
- RAG / vector search, long-term memory — decided separately
- Multi-tenancy and billing — handled per contract by a client-specific fork strategy (open, §8)
- Web frontend — API plus the `scripts/` demo client only
- **npm (JS/TS client SDK) release** — needs a separate new package, so it is deferred. Revisit after the PyPI release (goal 7) is stable (`docs/TASKS.md`, "Deferred" section)

## 4. Representative scenarios

1. **Query (no approval)** — "Which items at the main store will stock out next week?" → the agent calls `check_stockout` → streams a table summary. No interrupt.
2. **Side effect (approval required)** — "Send the reorder email for the at-risk items" → fetch suggestions → draft the email → **interrupt** (draft and recipient shown) → client calls `/approve` → send → report the result.
3. **Reject and revise** — at the interrupt, `approved=false` plus a comment → no send; a revised draft is proposed again, or the agent ends politely.
4. **Restart resilience** — with scenario 2 paused at the interrupt, restart the server → `/approve` with the same thread_id → resumes and sends normally.

## 5. Success criteria (v0.1 done)

- Scenarios 1–4 all pass at the API level as **e2e-mock** (ScriptedChatModel + fake tools).
- A graph-structure test proves there is no path by which a side-effecting tool bypasses the approval gate.
- `make check` passes; coverage of `src/lang_ai_agent/core/` is 90% or higher.
- Manual smoke: one run with a real Claude model, optionally with a real retail-mcp stdio connection, reproducing scenario 1. — Done 2026-09-05: scenario 1 works on `anthropic:claude-sonnet-4-5` (tool call, token streaming, usage; one run ≈ 2.0k input / 0.3k output tokens ≈ $0.01). Two defects that only show up with a real model were found and fixed (`.env` not exported, block-list content).
- Onboarding: after install, `lang-ai-agent init` → `lang-ai-agent serve` alone gets a response with your own key (goal 8, T11).
- Portfolio material: English README draft plus a demo script (T11).
- A successful TestPyPI release and at least one production PyPI release (T13–T14).

## 6. Roadmap

| Version | Contents | Prerequisite |
|---|---|---|
| v0.1 | Single-agent graph + approval gate + FastAPI SSE + deterministic tests + onboarding CLI + GitHub Actions CI (`make check`) + PyPI release + public launch prep | — |
| v0.2 | PostgresSaver, supervisor multi-agent, always-on real retail-mcp connection, per-thread lease and effect idempotency keys for multi-process, message window/summary, checkpoint pruning, thread TTL, dependency vulnerability scan in CI (DESIGN §11) | v0.1 public |
| v0.3 | Evaluation harness (golden-trajectory regression + eval sets), cost reports, Docker deployment template | — |
| v0.4 | Decide on folding into the MCP core — move the core's vertical agents onto this runtime | core MVP validated |

## 7. Portfolio deliverables (at public launch)

- README in English (all internal docs are in English as well, since 2026-09-05), one architecture diagram, one 60-second demo (terminal recording).
- A "why it is built this way" section: approval gate, deterministic tests, keeping state small — reasons you can state verbatim in a contract interview.
- README carries a PyPI badge plus `pip install` / `uv add` instructions (T15).
- License **MIT** (settled 2026-09-05: the whole dependency stack is MIT, so it is consistent and the lightest to review for a contracting client). The `LICENSE` file is added in T13.

## 8. Open items

- [x] Default model tier (cost vs demo quality) — user decision 2026-09-05: **keep** `anthropic:claude-sonnet-4-5` (one smoke run ≈ $0.01, 8 s response, demo quality sufficient). Compare other tiers in v0.3 evals if it becomes necessary.
- [ ] Process management (stdio lifetime) for an always-on retail-mcp connection
- [ ] Contract delivery standard: per-client fork vs core library — decide before v0.2
