# 001 — Exhaustive adversarial code audit report

- Audit date: 2026-09-05 (Asia/Manila)
- Scope: all 53 tracked Python files (`src/` 18, `tests/` 34, `scripts/` 1),
  plus all packaging, quality and runtime configuration
- Method: static analysis, strict type checking, full test run with branch coverage, bytecode compilation,
  offline sdist/wheel build, manual tracing of operational failure paths
- Conclusion: no syntax, type, lint or existing-test failures, but nine findings in total, including two
  high-risk concurrency defects and one state-correctness defect that must be fixed before production.

## 1. Automated checks

| Check | Result |
|---|---|
| `ruff check .` | pass, 0 errors |
| `ruff format --check .` | pass, 55 files formatted |
| `pyright` (strict) | pass, 0 errors and warnings |
| `pytest` | pass, 176 tests |
| Total line/branch coverage | 100% / 100% |
| `core/` quality gate | 100%, requirement 90% or higher |
| `python -m compileall -q src tests scripts` | pass |
| `uv build --offline` | sdist and wheel built successfully |

The default uv cache outside the sandbox was not accessible, so `UV_CACHE_DIR=/tmp/hawkfish-uv-cache`
was used. Not a code defect; the whole gate passed normally in the same environment.

Dependency CVE scanning was not included: `pip-audit`, `bandit` and `semgrep` were not installed locally and
the audit ran without network access. Therefore "no vulnerable dependencies" is not guaranteed.

### Files covered

- Product code: all 18 `src/lang_ai_agent/**/*.py`
- Tests and test helpers: all 34 `tests/**/*.py`
- Executable script: `scripts/smoke.py`
- Configuration that affects code behavior: `pyproject.toml`, `uv.lock`, `Makefile`, `.python-version`,
  `.github/workflows/ci.yml`, `.env.example`, `mcp_servers.json.example`, `.gitignore`
- Excluded: generated or externally managed files such as `.venv`, `dist`, caches, `__pycache__`,
  `.coverage`, `.DS_Store` and Git internal objects

Markdown documents were used as reference material to check for mismatches between the implementation
contract and the code, but the audit was not limited to what the documents describe. Every product Python
file went through lint, strict type check and compile, and the test run executed all 18 product files at
100% line and branch coverage.

## 2. Findings

### AUD-001 [High] Concurrent handling of the same approval can execute a side effect twice

- Location: `src/lang_ai_agent/api/app.py:211-227`, `src/lang_ai_agent/core/graph.py:267-305`
- Status: code path confirmed; no concurrency load test exists
- Evidence: `/approve` does not make the state lookup and the `Command(resume=...)` execution atomic. If two
  requests arrive concurrently for the same `thread_id`, both can check `state.interrupts` and then resume the
  same approval checkpoint. The external email/API call happens before the checkpoint is written, so even
  with an optimistic conflict the side effect that already happened cannot be undone.
- Impact: duplicate emails, duplicate orders or payments. Fatal once a real effect tool is attached.
- Recommendation: a single execution lock per `thread_id`; when scaling out, a DB-based lease/transaction
  together with an effect idempotency key (`tool_call_id`). Record approval consumption atomically as well.

### AUD-002 [High] Messages and approvals on the same thread are not serialized

- Location: `src/lang_ai_agent/api/app.py:183-193`, `src/lang_ai_agent/api/app.py:211-227`
- Status: code path confirmed; no race-condition test exists
- Evidence: message sending and approval share the same checkpointer but there is no per-thread lock.
  `/messages` is allowed even while an approval is pending, and the request submits a new input with
  `pending=None`. Two messages, or a message and an approval, overlapping can create forked checkpoints and
  model calls on the same thread.
- Impact: lost approvals, reordered messages, duplicate model and tool calls, polluted usage.
- Recommendation: serialize execution per thread, and either reject `/messages` during an interrupt with 409
  or define a separate API for "explicitly cancel the existing approval, then start a new turn".

### AUD-003 [Medium] The thread's accumulated usage resets on every new message

- Location: `src/lang_ai_agent/api/app.py:187-191`, `src/lang_ai_agent/core/graph.py:241-243`
- Status: confirmed
- Evidence: every `/messages` request inputs `usage=Usage()`. `usage` has no reducer, so the accumulated
  value in the existing checkpoint is overwritten with zero and only that request's model calls are added
  back. The documents and the model state "accumulated per thread", but no multi-turn test verifies it.
- Impact: under-counted cost and token observations; wrong budget/billing decisions.
- Recommendation: read the existing state and preserve the total, or define a usage-specific reducer. Add an
  API test that verifies the accumulated value over at least two user turns.

### AUD-004 [Medium] Messages and checkpoints grow without bound

- Location: `src/lang_ai_agent/core/state.py:40-50`, `src/lang_ai_agent/adapters/checkpoint.py:53-70`
- Status: confirmed design risk
- Evidence: `add_messages` keeps accumulating the thread's whole message list, and the SQLite checkpointer
  has no retention period, maximum turn count, compaction/summary, or delete API. Past checkpoints of every
  run are stored too.
- Impact: under long-running use, model context, process memory, the SQLite file and latency keep growing.
  Less a classic unreleased-object leak than a logical resource leak through unbounded retention.
- Recommendation: add a message window/summary policy, thread TTL and checkpoint pruning, a delete API, DB
  size metrics and limits.

### AUD-005 [Medium] No size limits on requests or tool arguments allows memory/disk DoS

- Location: `src/lang_ai_agent/api/app.py:114-120`, `src/lang_ai_agent/core/graph.py:272-277`
- Status: confirmed
- Evidence: messages, approval comments, and model-generated effect arguments have no maximum length.
  `PendingAction` copies the whole argument set with `args_preview=dict(call["args"])`, email body included,
  into the checkpoint and the response.
- Impact: if the single auth token leaks or a client malfunctions, large requests/responses can exhaust
  memory and the DB quickly.
- Recommendation: ASGI/body limits and Pydantic `max_length`, per-argument limits for tools, and preview
  truncation or hashing/external storage.

### AUD-006 [Medium] Exceptions after the SSE stream ends are not turned into an `error` event

- Location: `src/lang_ai_agent/api/sse.py:147-178`
- Status: confirmed
- Evidence: the `try/except` wraps only the `graph.astream_events(...)` iteration. DB, serialization and
  schema errors raised afterwards in `graph.aget_state`, interrupt payload dereferencing and usage
  dereferencing escape untouched. This differs from the function docstring's guarantee "Any exception ...
  becomes a single error event".
- Impact: the client sees only a closed connection, with no proper `error`/`done` terminal event.
- Recommendation: include post-processing in the exception boundary, and add a test that guarantees exactly
  one `error` terminal event even after some events were already sent.

### AUD-007 [Medium] Internal exception strings are exposed verbatim through the API

- Location: `src/lang_ai_agent/core/graph.py:175-180`, `src/lang_ai_agent/api/sse.py:168-170`
- Status: confirmed
- Evidence: `str(exc)` of tool and graph exceptions is passed directly into ToolMessage/SSE. Errors from
  external SDKs or MCP can contain URLs, file paths, parts of requests, and environment details.
- Impact: internal structure or sensitive data is exposed to authenticated clients, and the string goes back
  into the model context.
- Recommendation: use stable error codes and sanitized messages externally; record detailed tracebacks only
  in server logs with a correlation ID.

### AUD-008 [Low] Bearer token comparison is not constant-time

- Location: `src/lang_ai_agent/api/auth.py:30-40`
- Status: confirmed; realistic exploitability is low depending on the deployment
- Evidence: plain string `!=` is used.
- Impact: a timing side channel is possible in environments that allow very precise repeated measurement.
- Recommendation: use `secrets.compare_digest(credentials.credentials, expected_token)`.

### AUD-009 [Low] `.env` update is not atomic and follows symbolic links

- Location: `src/lang_ai_agent/cli.py:83-102`
- Status: confirmed; local CLI attack surface
- Evidence: after `touch`/`chmod`, `write_text` truncates the existing file in place. A failure midway leaves
  an empty or partial file, and with `--force` a symlink target is overwritten.
- Impact: loss of configuration or keys, or unintended file changes in the local environment.
- Recommendation: create a mode-0600 temp file in the same directory, `fsync`, then `os.replace`; refuse
  symlinks explicitly.

## 3. Areas that passed but need further confirmation

- Real LLMs, real MCP and external networks were not exercised, per the repository guardrails.
- MCP tool loading runs sequentially per server with no explicit startup timeout
  (`src/lang_ai_agent/adapters/mcp_loader.py:151-165`). A hung server can block application startup
  indefinitely, so a timeout/partial-failure policy is needed.
- `POST /threads` only issues an ID and stores nothing, so calling `/messages` with an arbitrary string
  creates a new thread. Under the current single shared-token design this is not a direct privilege
  escalation, but the API contract and the 404 guidance disagree with the actual behavior.

## 4. Fix priority

1. AUD-001/002: implement per-thread serialization together with effect idempotency.
2. AUD-003: fix multi-turn usage accumulation and add a regression test.
3. AUD-004/005: define lifetimes and size limits for input, state and checkpoints.
4. AUD-006/007: fix the SSE error boundary and external error sanitization.
5. AUD-008/009 and the MCP timeout as defensive hardening.

The current result is "automated quality gate passed", not "no operational defects". In particular, AUD-001
and AUD-002 must be resolved before a real side-effecting tool is connected.

## 5. Actions taken (2026-09-05, T16 — `docs/TASKS.md`)

Each item was re-verified against the code before acting. Verdict: all nine valid. Regression tests are in
`docs/TESTING.md` §4 "Audit 001 response"; design updates in `docs/DESIGN.md` §2/§3/§5/§6/§7/§11.

| ID | Action | Location |
|---|---|---|
| AUD-001 | Fixed: per-thread_id `asyncio.Lock` (`ThreadLocks`) serializes the `/messages` and `/approve` streams and `DELETE`. The approval target (`tool_call_id`) is re-checked inside the lock, so a duplicate request ends with a single `error` event (or 409 from the check outside the lock). DB lease and effect idempotency key for multi-process are v0.2 (DESIGN §11) | `api/thread_locks.py`, `api/app.py` |
| AUD-002 | Fixed: same lock. `/messages` while waiting for approval is 409 (a rejection comment is the cancellation; then a new turn) | `api/app.py` |
| AUD-003 | Fixed: `usage` gets the summing reducer `add_usage`; the agent node returns only the increment of one call (`usage_of_call`). Two-turn accumulation tests at graph and API level | `core/state.py`, `core/graph.py` |
| AUD-004 | Partial: `DELETE /threads/{id}` (checkpointer `adelete_thread`) added. Message window/summary, checkpoint pruning and TTL, size metrics carried over to v0.2 (SPEC §6, DESIGN §11) | `api/app.py` |
| AUD-005 | Fixed: `content` ≤ 8,000 characters, `comment` ≤ 2,000 (422); Content-Length over 64 KiB gets 413 from a pure ASGI middleware before the body is read. `args_preview` became a 120-characters-per-value summary and execution re-fetches the original tool_call by id — this also fixed the derived defect of executing the preview as arguments. The `draft` is kept whole because the human has to see all of it | `api/limits.py`, `core/graph.py`, `core/state.py` |
| AUD-006 | Fixed: post-stream processing (`aget_state`, interrupt and usage lookup) moved inside the exception boundary. Test for exactly one `error` even after some events were sent | `api/sse.py` |
| AUD-007 | Fixed: the exposed string is only `describe_error()` (exception type plus first line, 200 characters). Full traceback in the log (`exc_info`, thread_id, tool, error_type). Stable error codes will be added to the SSE schema when needed | `core/errors.py`, `core/graph.py`, `api/sse.py`, `adapters/mcp_loader.py` |
| AUD-008 | Fixed: `secrets.compare_digest` (byte comparison — no exception on non-ASCII input) | `api/auth.py` |
| AUD-009 | Fixed: temp file in the same directory (0600, `mkstemp`) + `fsync` + `os.replace`; on failure the temp file is removed and the original kept; symlinks are refused even with `--force` | `cli.py` |
| §3 MCP | Fixed: 30-second per-server startup timeout (`startup_timeout_s`); on timeout or failure an `McpConfigError` with the server name and command | `adapters/mcp_loader.py` |
| §3 threads | Documented: thread ids are a client-chosen namespace, an intended design now stated in DESIGN §5 | `docs/DESIGN.md` |

Verification: `make check` passed — ruff and pyright strict 0 errors, 208 passed (176 before the audit), 100% line and branch coverage. The concurrency tests (AUD-001/002) were run five times to confirm stability.
