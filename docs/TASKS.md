# TASKS — lang_ai_agent v0.1 backlog

## How to use

- One agent session = one task. Prompt template:
  > Read `docs/SPEC.md`, `docs/DESIGN.md` and `docs/TESTING.md`, then do **T5**. Keep fixing until every completion criterion is met and `make check` passes. When done, summarize the changed files and the verification results.
- Every completion criterion is machine-checkable. On completion: status `DONE(date)` + commit (`T{n}: summary`).
- Parallel lanes: after T1, **A (T2), B (T3), C (T4)** can run concurrently in separate worktree agents. T5 is the hub; **T6/T8/T9** parallelize again afterwards.

Dependency graph: `T0 → T1 → {A: T2, B: T3, C: T4} → T5 → {T6, T8, T9} → T7(T5,T6) → T10(T7,T8,T9) → T11 → T16 → T13 → T14 → T15 → T17`. T12 (docs) has no prerequisite — ran independently, DONE. T16 (audit 001 response) was inserted after T11 and before T13 following the user's code review. T17 (English-only repo + 0.1.1 release) was added after T15 by user decision.

---

### T0 — Project scaffolding · Status: DONE(2026-09-04)
- Goal: uv project (pyproject), ruff, pyright (strict), pytest (+asyncio, cov) configuration, Makefile (check/test/lint/typecheck/dev/smoke), `.env.example`, `.gitignore` (.env, data/).
- Criteria: [x] `make check` passes [x] one dummy async test runs [x] pyright strict confirmed (a snapshot test that catches an intentional type error) [x] git init + first commit

### T1 — State, types, tool convention · Status: DONE(2026-09-04) · Depends on: T0
- Goal: `core/state.py` (AgentState, PendingAction, Usage), `core/tools_spec.py` (ToolSpec, safe/effect classification), SSE event Pydantic schema (the type part of `api/sse.py`).
- Criteria: [x] all models pass pyright; serialization round-trip test [x] the no-large-payload rule for state stated in the docstring [x] check passes

### T2 (lane A) — ScriptedChatModel + script builder · Status: DONE(2026-09-04) · Depends on: T1
- Goal: the ScriptedChatModel of TESTING §2 (BaseChatModel subclass, tool_calls replay, assert_exhausted) and the `script()` builder.
- Criteria: [x] tests for clear failure messages on exhausted, leftover and mismatched scripts [x] replay test of an AIMessage with tool_calls [x] check passes

### T3 (lane B) — Tool layer · Status: DONE(2026-09-04) · Depends on: T1
- Goal: three built-in fakes (retail-mcp schema mirror, `fail_on` injection), `adapters/effects.py` (SEND_MODE double gate) + MockEffects.
- Criteria: [x] zod-grade argument validation (Pydantic args_schema) [x] test that dry_run never enters the real-send path [x] check passes

### T4 (lane C) — Checkpointer and model factory · Status: DONE(2026-09-04) · Depends on: T1
- Goal: `adapters/checkpoint.py` (InMemory/Sqlite selection), `adapters/llm.py` (init_chat_model, MODEL env), thread config utilities.
- Criteria: [x] temp-file AsyncSqliteSaver save/restore test [x] invalid MODEL string → error containing the fix [x] check passes
- Revision (found during T5): the graph runs async, so the synchronous `SqliteSaver` raises `NotImplementedError` in `ainvoke`/`aget_state` — replaced with `AsyncSqliteSaver` (the checkpointer notes below were corrected the same way).

### T5 — Graph core · Status: DONE(2026-09-04) · Depends on: T2, T3, T4
- Goal: `core/graph.py` — the DESIGN §3 topology (agent/route/safe_tools/approval+interrupt/effect_tools), compile factory.
- Criteria: [x] **all four TESTING §3 golden trajectories** [x] **all five §4 graph and approval-gate items** (structural invariant included) [x] check passes
- Bonus: the unregistered-tool and tool-exception handling of TESTING §4 "Tools and errors" was implemented and tested here too (inseparable from the graph implementation; not in the T5 criteria but covered).
- Revision: corrected T4's checkpointer to `AsyncSqliteSaver` (see T4) and registered `PendingAction` in the msgpack deserialization allow-list (without it a "will be blocked in a future version" warning appears and restart recovery could break in a future langgraph version).

### T6 — SSE event mapper · Status: DONE(2026-09-04) · Depends on: T5
- Goal: `astream_events` → internal event stream conversion (api/sse.py), event ordering guaranteed.
- Criteria: [x] event order and schema tests on a scripted stream [x] interrupt event carries pending [x] check passes
- Revision: (1) added `_stream`/`_astream` to ScriptedChatModel (T2) — a model without streaming never emits `on_chat_model_stream`, so token-event mapping could not be tested. (2) `Usage` added to the msgpack allow-list too (T5 registered only `PendingAction`, so `Usage` was being blocked silently) — found while reading interrupt state (`aget_state`).
- Design note: `astream_events` does not expose graph interrupts as events — after the stream ends, `aget_state(config).interrupts` is checked separately. `tool_call_id` is the `astream_events` run_id, not the model's actual tool_call.id (the former is unknown at on_tool_start).

### T7 — FastAPI service · Status: DONE(2026-09-05) · Depends on: T5, T6
- Goal: four endpoints + Bearer auth + SSE responses. app.py is assembly only (no logic).
- Criteria: [x] **all four TESTING §4 API and SSE items** (httpx ASGI) [x] `make dev` starts [x] check passes
- Design note: `create_app(graph_factory, bearer_token)` (injection, tests) / `create_default_app()` (`.env` → Settings → real model + AsyncSqliteSaver). `make dev` calls the latter through uvicorn `--factory` so the environment is not read at import time. A thread comes into existence at the first `/messages` in the checkpointer; existence is judged by `aget_state().values`. `/approve` on a thread with no pending interrupt is 409. Settings via pydantic-settings; SSE framing via sse-starlette (`event:` = event type, `data:` = JSON).

### T8 — usage and observability · Status: DONE(2026-09-05) · Depends on: T5
- Goal: token accounting → state usage → exposed via SSE/state, structured JSON logs, LANGSMITH_TRACING wiring.
- Criteria: [x] usage accumulation test (TESTING §4) [x] log snapshot containing thread_id, node, tool [x] check passes
- Design note: instead of a callback, the agent node accumulates the response's `usage_metadata` through a pure function (DESIGN §7 note). The log clock is injected via `build_graph(clock=)` — tests use `tests/helpers/fixed_clock.py` for exact duration_ms snapshots. ScriptedChatModel (T2) takes fixed per-turn usage (`input_tokens=`/`output_tokens=`) and, when streaming, puts usage_metadata **only on the last chunk** (LangChain sums chunk usage, so putting it on every chunk multiplies it — measured). LangSmith reads `LANGSMITH_TRACING` directly, but pydantic-settings does not export `.env` to `os.environ`, so writing it back at startup is the "wiring".

### T9 — MCP loader · Status: DONE(2026-09-05) · Depends on: T5
- Goal: `mcp_servers.json` parser + `MultiServerMCPClient` loader + approval mapping (unlisted tools → effect default), `.example` file.
- Criteria: [x] parsing and mapping unit tests (no real process) [x] missing configuration file → error containing the fix [x] check passes
- Design note: three layers — parsing (Pydantic, `extra="forbid"` so a typo like `aproval` cannot silently become "everything is effect") / mapping (pure) / connection (`load_mcp_tool_specs`, client factory injectable → tests use a fake). Per-server `get_tools(server_name=)` applies the policy precisely. `core/tools_spec.merge_tool_specs` **rejects duplicate tool names at startup** across built-ins, MCP and between servers (the graph looks tools up by name, so a later one would silently override an earlier one's approval requirement). v0.1 is stdio only. App wiring: when `MCP_SERVERS_PATH` (optional, added to DESIGN §8) is set, load and merge at startup — the entry point of the T11 smoke `--mcp`. This version of `MultiServerMCPClient` dropped the context manager (a session per call), so there is no lifetime for the loader to hold.

### T10 — e2e-mock + coverage · Status: DONE(2026-09-05) · Depends on: T7, T8, T9
- Goal: SPEC §4 scenarios 1–4 as API-level e2e-mock (restart resilience included), coverage report.
- Criteria: [x] all four scenarios pass [x] core ≥ 90% report attached [x] check passes
- Scenarios (`tests/e2e/test_scenarios.py`, through the real app via httpx ASGI): 1 query (zero interrupts, usage total) · 2 approved send (safe lookup → interrupt with draft and recipient → /approve → send → result report, dry_run second gate) · 3a reject → polite ending · 3b reject with comment → **revised draft re-proposed (second interrupt)** → approve → only v2 sent · 4 restart resilience (temp SQLite: app, graph and checkpointer fully discarded, then a new app restores `/state` and sends via `/approve` on the same thread_id) · thread isolation (TESTING §4).
- Coverage report (attached): `make test` gates with `coverage report --fail-under=90 --include='src/lang_ai_agent/core/*'`. Result — core/graph.py 130 stmts/18 br 100%, core/state.py 16/0 100%, core/tools_spec.py 14/2 100%, **core total 160 stmts/20 br, 0 missed → 100%**; whole project 546 stmts/68 br 100%, 139 passed.

### T11 — Smoke + portfolio prep · Status: DONE(2026-09-05) · Depends on: T10
- Goal: `scripts/smoke.py` (real-model scenario 1 + console approval, `--mcp` for the real retail-mcp), English README draft, 60-second demo script scenario, GitHub Actions `ci.yml` (make check).
- Extension (confirmed with the user): self-service onboarding (SPEC goal 8, DESIGN §8.1) — `lang-ai-agent init/serve/smoke` console script, provider key check at startup (`ConfigError`), OpenAI/xAI/Google SDKs as base dependencies.
- Criteria: [x] smoke procedure in the README within five lines (three) [x] ci.yml syntax verified (YAML parsing; act not installed) [x] demo scenario documented (`docs/DEMO.md`) [x] check passes (176 passed, core 100%, total 100%)
- Real-model smoke result (user's key, `anthropic:claude-sonnet-4-5`, dry_run, two billed calls ≈ $0.02, key deleted afterwards): scenario 1 fine — tool call, token streaming, usage (one run ≈ 2.0k input / 0.3k output tokens, 8 s). Two defects only visible with a real model were fixed: (1) pydantic-settings does not export `.env` to `os.environ`, so the provider SDK could not see the key → `load_dotenv` at startup; (2) real-model content is a block list, so zero token events and `last_message` None → `api/sse.py: content_text()`. Both pinned with regression tests.
- Design note: smoke logic lives in `lang_ai_agent/smoke.py` (`run_scenarios` takes injected console callbacks and is tested with a scripted model); `scripts/smoke.py` is a wrapper. `run_smoke` forces `SEND_MODE` to dry_run, does not load MCP without `--mcp`, and assumes the `.env` written by `init` (it does not touch environment variables). `init` creates `.env` at 0600 (`touch(mode=)` then write — no permission window), an existing file needs `--force`, the key is read only through `getpass`. The provider table (`adapters/llm.py: PROVIDERS`) covers anthropic/openai/xai/google_genai — providers outside the table skip the startup check. `serve` and `smoke` reuse `create_default_app`/`open_default_graph` (no duplicated assembly).
- User decisions (2026-09-05, after merge): keep the default model `claude-sonnet-4-5` (resolves SPEC §8), keep the model names `init` suggests, the agent does the final pass on the README's English tone (eight wording tweaks, no change to structure or claims).

### T12 — Document the PyPI and npm release direction · Status: DONE(2026-09-04) · Depends on: none
- Goal: reflect "PyPI release" as an official v0.1 goal in SPEC/DESIGN/WORKFLOW/README, and state the npm (JS/TS client SDK) release as a v0.1 non-goal, deferring its start. Direction settled in conversation with the user.
- Criteria: [x] SPEC §2/§3/§5/§6/§7 updated [x] DESIGN §10 Distribution (Packaging) section added [x] WORKFLOW §4 limits of autonomy gains "production PyPI release approval" [x] README status log updated [x] confirmed no code changes (docs only)

### T13 — PyPI packaging · Status: DONE(2026-09-05) · Depends on: T11, T16
- Goal: finalize `pyproject.toml` distribution metadata (description/license/classifiers/urls/authors), verify sdist+wheel via `uv build`, trial release on TestPyPI. The console script (`[project.scripts] lang-ai-agent`) was pre-applied in T11 — whether `lang-ai-agent init` works after install is part of the TestPyPI install check.
- Prior decisions (2026-09-05, docs first — `docs/RELEASE.md`): license **MIT** → adding the `LICENSE` file moved from T15 to T13. Auth is **Trusted Publishing** → `.github/workflows/publish.yml` (build + `testpypi` job) written in T13. The TestPyPI upload happens with a `v0.1.0rcN` tag after a human completes RELEASE.md §1 (accounts, pending publishers, GitHub Environments). `uv build` already succeeded as of 2026-09-05 (only METADATA License/Classifier/Project-URL/Keywords were missing); no `src/` code changes.
- Criteria: [x] `uv build` output (sdist+wheel) confirmed [x] `LICENSE` (MIT) and metadata present in the wheel METADATA (`License-Expression: MIT`, `License-File`, classifiers, Project-URL, Keywords; LICENSE in both sdist and wheel) [x] `publish.yml` syntax verified (YAML parsing, build + `testpypi` job) [x] TestPyPI upload succeeded (tag `v0.1.0rc1` → Publish run 33956057465, build and testpypi jobs both success — normal on the first Trusted Publishing run) [x] installed into an isolated venv with `--index-url` TestPyPI; `lang-ai-agent --help`, version `0.1.0rc1` and `License-Expression: MIT` verified; wheel+sdist registered on the project page [x] check passes (208 passed, 100%)
- Implementation note: the version was bumped with `uv version 0.1.0rc1`, updating pyproject and uv.lock together (the final `0.1.0` is T14), and `__version__` reads `importlib.metadata` so nothing is hardcoded (the scaffolding test compares against the pyproject value). The built wheel is verified in an isolated environment outside the project (`uv run --isolated --no-project --with dist/*.whl`) for the console script and version. The `publish.yml` build job checks tag↔version → `make check` → `uv build` → wheel smoke → artifact upload; the `testpypi` job uses environment `testpypi` + `id-token: write` with `pypa/gh-action-pypi-publish`. The license classifier is omitted because it duplicates the PEP 639 expression (DESIGN §10).

### T14 — Production PyPI release workflow · Status: DONE(2026-09-05) · Depends on: T13
- Goal: a production PyPI release pipeline on tag push via GitHub Actions + Trusted Publishing (OIDC). **Every production release is triggered only after maintainer approval** (WORKFLOW §4). Implemented by adding the `pypi` job to T13's `publish.yml` (environment `pypi` with Required reviewers = the approval; runs only on final tags). Tag and version rules pre-documented in `docs/RELEASE.md` §3 (2026-09-05).
- Criteria: [x] workflow yml syntax verified (YAML parsing + job structure check; actionlint not installed) [x] version tag rules documented (RELEASE.md §3) [x] one production release under maintainer approval succeeded (`v0.1.0` → Publish run 33957885151, testpypi and pypi jobs both success, the user approved environment `pypi`; `lang-ai-agent 0.1.0` wheel+sdist on pypi.org, isolated install, `--help` and MIT verified — RELEASE.md §5) [x] check passes
- Implementation note: the `pypi` job is `needs: [build, testpypi]` + `if: needs.build.outputs.prerelease == 'false'`. The build job's tag-check step computes `is_prerelease` with `packaging.version` and passes it as a job output — deciding by the presence of `rc` in the tag string would let `a1`/`b1`/`.dev1` leak through as final, so PEP 440 parsing decides (RELEASE.md §2). Environment `pypi` with Required reviewer (Trapa-Eureka) and tag rule `v*` was confirmed via the GitHub API. Version bumped with `uv version 0.1.0` (pyproject + uv.lock). No `src/` code changes.

### T15 — Final public launch · Status: DONE(2026-09-05; the terminal recording is left to a human) · Depends on: T11, T14
- Goal: add a PyPI badge and install commands to the root README; check every item of the GitHub launch checklist (SPEC §7). LICENSE is MIT and was added in T13, so here only its existence and copyright holder are verified.
- Criteria: [x] README has a PyPI badge + `pip install`/`uv add` instructions (four badges: PyPI version, Python versions, MIT, CI — all return 200, the version badge shows `v0.1.0`; the Install section has `pip install`/`uv add`/`uv tool install`, the checkout path is `uv sync`) [x] LICENSE (MIT) exists with the copyright holder (`Copyright (c) 2026 Trapa-Eureka`, matching the sdist, wheel and PyPI metadata `License-Expression: MIT`) [x] SPEC §7 checked item by item — table below
- SPEC §7 check:

  | Item | Result |
  |---|---|
  | README in English (internal docs Korean at the time) | Met — English README; `docs/` and CLAUDE.md were Korean until T17 switched them to English |
  | One architecture diagram | Met — the mermaid graph in the README's "How it works" (rendered on GitHub; shown as a code block on PyPI) |
  | One 60-second demo (terminal recording) | **Not met (human task)** — the script is in `docs/DEMO.md`, but recording needs a real model key, which the agent cannot use. After recording, add the link or GIF to the README's "60-second demo" section |
  | "Why it is built this way" section (approval gate, deterministic tests, keeping state small) | Met — seven items in the README's "Why it is built this way" |
  | PyPI badge + `pip install`/`uv add` | Met — this task |
  | License MIT + `LICENSE` | Met — T13 |

- Also tidied: the README Status line became "v0.1.0 released on PyPI", the License section `[MIT](LICENSE) © 2026 Trapa-Eureka`, the Quickstart and Smoke commands are written for an installed package (no `uv run` prefix) and the checkout path moved to Development. The README reaches the PyPI project page with the next version (0.1.1 or later; the 0.1.0 distribution's README cannot be re-uploaded — RELEASE.md §3). No `src/` code changes.
- GitHub About (human, done 2026-09-05): the repo description now carries the README's one-line summary and the website points at https://pypi.org/project/lang-ai-agent/ (verified with `gh repo view`). Topics are still empty — optional.

### T16 — Audit 001 response · Status: DONE(2026-09-05) · Depends on: T11 (done before T13)
- Goal: re-verify the user's code review report `docs/001_ADVERSARIAL_CODE_AUDIT.md` (nine findings + two extras) against the code, fix the valid ones, and document what remains for v0.2 (DESIGN §11).
- Criteria: [x] all 11 items judged and acted on (report §5 table) [x] regression tests (TESTING §4 "Audit 001 response") [x] DESIGN §2/§3/§5/§6/§7/§11, SPEC §6 and CLAUDE.md updated [x] check passes (208 passed, 100% line and branch coverage, the race-condition tests stable over five repeated runs)
- Verdict: all nine valid — AUD-001/002 (per-thread serialization + 409 for `/messages` while awaiting approval), 003 (`add_usage` reducer), 004 (partial: `DELETE /threads/{id}`, the rest v0.2), 005 (request limits, preview summary), 006 (SSE exception boundary), 007 (`describe_error`), 008 (`compare_digest`), 009 (atomic `.env`). Two extras: MCP startup timeout fixed; `POST /threads` storing nothing is intended design, now stated in DESIGN §5.
- A derived defect the audit missed: `effect_tools` was executing `pending.args_preview` instead of the original tool_call — fixed together with the preview summary by re-fetching the original (DESIGN §2/§3).
- Design note: the lock is taken inside the stream generator so it is released with the response; the handler's check outside the lock (HTTP status) and the re-check inside the lock (`error` event) are separate. The body limit is a pure ASGI middleware (BaseHTTPMiddleware wraps the SSE response and interferes with disconnect detection). `usage_after_call` was replaced by `usage_of_call` (increment).

### T17 — English-only repo + 0.1.1 release · Status: DONE(2026-09-05) · Depends on: T15
- Goal (user request, 2026-09-05): (1) get the current README onto GitHub and PyPI; (2) translate every Korean string in the repo — Markdown docs, code comments, docstrings, test fixtures and user-facing strings — into English; (3) check that the "human approval" wording has nothing to do with the people who install the package, and reword where it could be misread.
- Criteria: [x] zero Hangul characters in tracked files (`git grep -P '[\x{AC00}-\x{D7AF}]'` is empty) [x] CLAUDE.md, WORKFLOW and SPEC §7 record the English-only rule [x] release-gate wording says "maintainer approval"; the agent's HITL gate stays "human approval", and DESIGN §10 / WORKFLOW §4 state explicitly that installers are never asked for approval [x] check passes (208 passed, 100% coverage; only test fixtures and two smoke prompts changed in code, no logic) [x] version `0.1.1` → PR #22 → tag `v0.1.1` → TestPyPI → maintainer approval → PyPI (Publish run 33961109346, all jobs success); the PyPI project page now shows the new README (badges, Install section, maintainer-approval wording verified) and an isolated install of `0.1.1` passes `--help`/MIT checks — RELEASE.md §5
- Finding for (3): "human approval" appears in two unrelated senses. (a) The agent's approval gate — the operator of a running backend approves their own agent's side-effecting tool over `/approve`; this is the product feature. (b) The release gate — the maintainer approves the `pypi` GitHub environment before a version is published. Neither involves the people who install the package; installing and running `lang-ai-agent` never requires approval from anyone. Wording for (b) was changed to "maintainer approval" throughout (README, DESIGN §10, WORKFLOW §4, RELEASE.md, the `publish.yml` job name).

---

## Deferred — npm (JS/TS client SDK) release

- 2026-09-04: deferred with the user's agreement (T12). The direction is a separate TypeScript client package wrapping this backend's HTTP+SSE API (DESIGN §5), published to npm; revisit after the PyPI release (T13–T15) stabilizes.
- Expected scope on resumption (draft; numbers assigned when resumed): SDK scaffolding (decide the location, e.g. `clients/typescript/`) → core client (type the DESIGN §5 contract + unit tests against a mock server) → npm release workflow.

## v0.2 queue (do not start — see the SPEC roadmap)

- PostgresSaver / supervisor multi-agent / always-on real retail-mcp connection (process lifetime management) / evaluation harness is v0.3
