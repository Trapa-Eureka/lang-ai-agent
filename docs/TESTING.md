# TESTING — lang_ai_agent

Purpose: verify every path of the agent graph locally and deterministically, **without a real LLM**. The core of shift-left in an LLM app is "replace the model with a script" — with a script in place the graph is just a state machine, and a state machine can be tested completely.

## 1. Principles

- Zero network and zero real-LLM calls in tests. Model = ScriptedChatModel, tools = fakes, sending = mock, checkpointer = InMemorySaver (restart tests only use a temp-file AsyncSqliteSaver — the graph is async, so the synchronous SqliteSaver cannot be used).
- When a test is flaky, **do not fix it by adding a real model** — fix the script or the code.
- `make check` = ruff + pyright + pytest. The whole thing finishes in seconds.
- Real-model verification belongs to `make smoke` (humans only) and to v0.3 evals.

## 2. Test helpers (tests/helpers/)

| Helper | Contents |
|---|---|
| `ScriptedChatModel` | Subclass of BaseChatModel. Takes a sequence of AIMessages (the script) in the constructor and returns them in order, one per call. May include tool_calls. **Fails clearly when the script is exhausted or left unconsumed** (assert_exhausted) |
| `script()` builder | Assembles a readable script: `script().tool_call("check_stockout", {...}).final("3 at-risk items at main...")` |
| Three fake tools | The DESIGN §4 built-ins — fixed responses plus `fail_on` injection to reproduce tool errors |
| `MockEffects` | Replaces the real implementation of send_reorder_email — records calls, used to verify the SEND_MODE gate |
| `FixedClock` / fixed usage | Determinism for time and token figures |

## 3. Golden trajectories (component — graph level)

The **node visit order** of each scenario is hardcoded to catch regressions.

- Query only: `agent → safe_tools → agent → END` (zero interrupts)
- Approved send: `agent → safe_tools → agent → approval(interrupt) ⏸ → [resume approved] → effect_tools → agent → END`
- Rejection: `... → approval(interrupt) ⏸ → [resume rejected+comment] → agent → END`, zero effect executions
- Mixed tool_calls (safe + effect in one response): all safe tools run first, then approval is entered

## 4. Required edge-case checklist

**Graph and approval gate**
- [x] Structural invariant: in the compiled graph, the only edge into effect_tools goes through approval (get_graph traversal)
- [x] The interrupt payload contains the pending summary and draft, not the large original
- [x] The resume value (`approved/comment`) is passed exactly as the return value of interrupt()
- [x] A rejection comment reaches the model as a ToolMessage (the follow-up response is verified by script)
- [x] With SEND_MODE=dry_run, MockEffects never enters the real-send path even when approved (double gate)

**Durability and restart**
- [x] Temp-file AsyncSqliteSaver: discard the graph object while interrupted → recompile (same DB and thread_id) → approve resumes successfully
- [x] Thread isolation: two thread_ids run concurrently with no state mixing

**Tools and errors**
- [x] A tool exception becomes an error ToolMessage; the graph does not die and the agent apologizes and offers an alternative per the script
- [x] A script that calls an unregistered tool → clear failure message
- [x] MCP loader: unit tests for configuration parsing and approval mapping (a tool missing from the configuration → effect default)

**API and SSE (httpx ASGI)**
- [x] Missing or wrong auth → 401
- [x] Event order of the messages stream: token* → tool_start/end* → (interrupt | usage → done)
- [x] After an interrupt, GET state exposes pending; after approve the stream runs through to done
- [x] Non-existent thread_id → 404 with the fix in the message

**usage**
- [x] Accumulated usage over multiple model calls equals the sum of the script's fixed usage

**Onboarding and configuration (T11)**
- [x] `lang-ai-agent init`: writes `.env` from injected console input (mode 0600, provider key and generated token included); an existing `.env` is refused without `--force`
- [x] Startup check: a missing key for `MODEL`'s provider → `create_default_app()` raises `ConfigError` at startup (variable name plus a pointer to `lang-ai-agent init`); passes when the key is present
- [x] SSE `token` and `/state`'s `last_message` also appear with real-model-shaped content (a list of content blocks) — block-list script
- [x] The smoke approval loop (`lang_ai_agent.smoke.run_scenarios`) is verified with a scripted model + MockEffects — zero real-model calls, `SEND_MODE` forced to dry_run

**Audit 001 response (T16 — `docs/001_ADVERSARIAL_CODE_AUDIT.md` §5)**
- [x] Two concurrent `/approve` calls for the same approval → the effect runs once; the loser gets 409 or a single `error` (AUD-001)
- [x] Two concurrent `/messages` on the same thread → serialized (message order H·A·H·A); `/messages` while waiting for approval → 409 (AUD-002)
- [x] Accumulated usage after two user turns = the sum of both turns (graph and API) (AUD-003)
- [x] `DELETE /threads/{id}` → 204, then `/state` 404; an id with no history → 404 (AUD-004 minimum)
- [x] Content-Length over 64 KiB → 413 (before body parsing); `content` over 8,000 characters → 422; long effect arguments are truncated only in the preview, the draft and the execution use the original (AUD-005)
- [x] `aget_state` failure after the stream ends → exactly one `error`, no `usage`/`done`, exc_info in the log (AUD-006)
- [x] Tool exceptions, stream exceptions and MCP failures expose only `Type: first line (≤200 chars)`; no paths or later lines (AUD-007)
- [x] Atomic `.env` write: on rename failure the original is kept and no temp file remains; symlinks are refused (AUD-009; AUD-008 `compare_digest` was confirmed by code review)
- [x] Unresponsive MCP server → timeout `McpConfigError` (server name and duration); server failure → server name plus first line (§3 MCP)

## 5. Manual smoke (humans only — scripts/smoke.py)

`make smoke` (= `lang-ai-agent smoke`): reproduces scenario 1 (query) and scenario 2 (send draft → interrupt → console y/n) with a real model. `SEND_MODE` is **forced** to dry_run regardless of `.env` — a real send (live) is not part of the smoke. With the `--mcp` flag, the real retail-mcp from `mcp_servers.json` (or `MCP_SERVERS_PATH`) is attached over stdio. One run costs three to four model calls (cents on a Sonnet-class model). The console loop itself is tested with a scripted model as in §4 "Onboarding and configuration"; running the real model is a human decision (WORKFLOW §4).

## 6. Coverage

- `src/lang_ai_agent/core/` at 90% or higher (pytest-cov, reported in T10). adapters are supplemented by the smoke.
- 2026-09-05 (T10): `make test` enforces this with `coverage report --fail-under=90 --include='src/lang_ai_agent/core/*'`. Currently core is 100% (graph 130/18, state 16/0, tools_spec 14/2 — zero misses), the whole project 100%. Every item in the §4 checklist is covered (graph and approval gate T5, durability and restart T10 e2e, tools and errors T5/T9, API and SSE T7, usage T8).
