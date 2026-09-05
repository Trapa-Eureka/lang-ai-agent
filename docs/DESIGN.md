# DESIGN — lang_ai_agent v0.1

This document is the source of truth for the implementation. Changes to the graph topology or the state schema are made here first.

## 1. Architecture

```
Client (curl / demo script / a future frontend)
      │ HTTP + SSE (Bearer auth)
      ▼
api/app.py (FastAPI — assembly only)
      │ ainvoke / astream_events / Command(resume)
      ▼
core/graph.py (StateGraph + checkpointer)
  agent ──(tool_calls?)──► route
    ▲                        ├─ safe_tools  ──► agent
    │                        └─ approval ──interrupt()──► [human] ──Command──► effect_tools ──► agent
    └──(no tool_calls)──► END
      │
      ├ adapters/llm.py         init_chat_model → Claude by default (model-agnostic)
      ├ adapters/checkpoint.py  InMemorySaver (tests) / AsyncSqliteSaver (dev and prod in v0.1)
      ├ adapters/mcp_loader.py  mcp_servers.json → MultiServerMCPClient → BaseTool[]
      └ adapters/effects.py     real implementations of side effects such as sending (SEND_MODE gate)
```

Core invariant: **the only edge into an effect tool passes through the approval node.** The topology is pinned by a test (TESTING §4).

## 2. State (core/state.py)

```python
class PendingAction(BaseModel):
    tool_call_id: str; tool_name: str
    args_preview: dict[str, JsonValue]   # summary shown to the human (no large payloads)

class Usage(BaseModel):
    input_tokens: int = 0; output_tokens: int = 0; calls: int = 0

class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    pending: PendingAction | None
    usage: Annotated[Usage, add_usage]   # summing reducer — a node emits only the increment of one call
```

Principle: state is serialized at every checkpoint — messages and minimal metadata only. Large tool results are summarized into the ToolMessage; the original stays outside the state.

- Implementation note (audit 001, T16): `usage` has a **summing reducer** `add_usage` — the `Usage()` that every `/messages` input carries adds zero, so the thread total survives across turns (previously a last-value channel that reset to zero on every new turn, AUD-003). `PendingAction.args_preview` is a **summary** produced by `_summarize_args` in `core/graph.py` (each value cut to 120 characters), not the execution arguments — `effect_tools` looks the original tool_call up again by `tool_call_id` in the messages and executes that (AUD-005; the previous implementation executed the preview as-is). The interrupt's `draft` is not truncated, because the human has to see the whole thing they are approving.

## 3. Graph (core/graph.py)

- `agent` node: calls `llm.bind_tools(all_tools)` → AIMessage. No tool_calls → END.
- `route` (conditional edge): classifies tool_calls into safe/effect. If one response mixes both, safe tools run first and the effect approval follows.
- `safe_tools` node: parallel execution allowed; appends the result ToolMessages → back to agent.
- `approval` node: records `pending`, then calls `interrupt({"action": pending, "draft": ...})` — the graph is saved and stops here. On resume, the value of `Command(resume={"approved": bool, "comment": str|None})` becomes the return value of interrupt().
  - approved → run `effect_tools` (the SEND_MODE gate is checked once more inside the effects adapter).
  - rejected → append the rejection reason as a ToolMessage and return to agent (whether to revise the draft or stop is the model's call).
- `effect_tools` node: finds the **original tool_call** in the last AIMessage by `pending.tool_call_id` and executes sequentially (the preview is only a summary, not the execution arguments — §2 note); result ToolMessage → back to agent.
- Compile: `graph.compile(checkpointer=...)`. Per-thread config `{"configurable": {"thread_id": ...}}`.

## 4. Tool classification convention (core/tools_spec.py)

```python
class ToolSpec(BaseModel):
    tool: BaseTool
    requires_approval: bool
```

- v0.1 built-ins (fakes mirroring the retail-mcp schema): `check_stockout` (safe), `get_reorder_suggestions` (safe), `send_reorder_email` (effect).
- Whether an MCP-loaded tool needs approval is mapped from the per-server, per-tool `requires_approval` settings in `mcp_servers.json`. **A tool absent from the configuration is treated as effect (approval required) by default** — the safe default.

## 5. HTTP API (api/)

| Method · path | Input | Behavior |
|---|---|---|
| `POST /threads` | — | Issue a thread_id |
| `POST /threads/{id}/messages` | `{content}` | Run the graph; return an **SSE stream** |
| `GET /threads/{id}/state` | — | Message summary, pending, usage |
| `POST /threads/{id}/approve` | `{approved, comment?}` | Resume with `Command(resume=...)`; return an SSE stream |
| `DELETE /threads/{id}` | — | Delete the thread's history (every checkpoint); 204 |

- Auth: `Authorization: Bearer $APP_BEARER_TOKEN` (single token in v0.1).
- SSE event types (api/sse.py — mapped from `astream_events`): `token` (model text delta) · `tool_start`/`tool_end` (name, duration) · `interrupt` (pending, draft) · `usage` · `done` · `error`. The event schema is pinned with Pydantic and tested.
- Implementation note (T6): `astream_events` does not expose graph interrupts as events — after the stream ends naturally, `aget_state(config).interrupts` is checked separately to build the `interrupt` event. The correlation id of `tool_start`/`tool_end` is the `astream_events` run_id, not the model's actual tool_call.id (the former is unknown at on_tool_start, so the latter stands in).
- Implementation note (T7): `POST /threads` only issues an id; the thread comes into existence when the first `/messages` writes the first checkpoint — a "non-existent thread" is one whose `aget_state().values` is empty (404). `/approve` on a thread with no pending interrupt is 409. SSE wire format: `event:` carries the event type, `data:` carries the event JSON (sse-starlette). The app is assembled by `create_app(graph_factory, bearer_token)`; the checkpointer (an async context manager) is opened by the lifespan for the app's lifetime; `make dev` launches `create_default_app()` through uvicorn `--factory`, so the environment (`.env` → pydantic-settings) is not read at import time.
- Implementation note (T11, found in the real-model smoke): real models such as Anthropic return message and chunk `content` not as `str` but as a **list of content blocks** (`[{"type": "text", "text": ...}]`). `content_text()` in `api/sse.py` extracts the text from either form to build `token` events and `/state`'s `last_message` (only `text` blocks count; `tool_use` blocks are ignored). ScriptedChatModel emits `str` only, so a block-list script test pins the regression — missing this is why the first smoke produced zero token events.
- Implementation notes (audit 001 response, T16 — `docs/001_ADVERSARIAL_CODE_AUDIT.md`):
  1. **Per-thread serialization**: `ThreadLocks` in `api/thread_locks.py` keeps one `asyncio.Lock` per thread_id (removed when idle); the SSE streams of `/messages` and `/approve`, and `DELETE`, run inside that lock. The handler does a quick check **outside** the lock to pick the HTTP status (404/409), and the stream re-checks **inside** the lock — of two concurrent `/approve` calls for the same approval, one runs and the other sees that the approval target (`tool_call_id`) is gone or changed and ends with a single `error` event (AUD-001). Single-process assumption (§11).
  2. `/messages` on a thread that is waiting for approval is 409 — answer with `/approve` first (a rejection comment is the cancellation) (AUD-002). A message that waited behind the lock and then met an interrupt ends with a single `error` for the same reason.
  3. **Request limits** (AUD-005): `content` ≤ 8,000 characters, `comment` ≤ 2,000 characters (422 beyond). A request whose Content-Length exceeds 64 KiB is rejected with 413 by the pure ASGI middleware in `api/limits.py` **before the body is read** (not BaseHTTPMiddleware, which would wrap the SSE response). A chunked body without Content-Length is read and then only the Pydantic limits apply.
  4. **Error sanitization** (AUD-007): the message of an `error` event is only `core/errors.describe_error()` — exception type plus the first line of the message (200 characters). The full traceback goes to the `lang_ai_agent.api` logger with `exc_info` and thread_id. Exceptions in post-stream processing (`aget_state`, interrupt and usage lookup) also end with **exactly one** `error` inside the same boundary (AUD-006).
  5. Thread ids are a **client-chosen namespace**: `POST /threads` only issues an id and stores nothing; the first `/messages` with any id creates the thread. `/state`, `/approve` and `DELETE` return 404 for an id with no history. Intended behavior under the single shared-token design (SPEC §3).
  6. `DELETE /threads/{id}` wipes the whole thread history through the checkpointer's `adelete_thread` — the only lifecycle management in v0.1 (the rest of AUD-004 is in §11).

## 6. MCP loader (adapters/mcp_loader.py)

`mcp_servers.json` (the example is committed as `.example`):

```json
{
  "retail": {
    "command": "npx", "args": ["tsx", "/path/to/retail-mcp/src/server.ts"],
    "transport": "stdio",
    "approval": { "default": "effect", "safe": ["sell_through", "inventory_status", "stockout_risk", "sync_status"] }
  }
}
```

The loader parses this configuration, loads the tools through `MultiServerMCPClient`, and maps them to ToolSpecs. **Unit tests cover parsing and mapping only** (no real process); the real connection is exercised in the smoke.

- Implementation note (T9): `approval` is `{default: "safe"|"effect" (default effect), safe: [...], effect: [...]}` — the same tool in both lists is a configuration error. Server entries are parsed with `extra="forbid"` so that a typo in a key cannot silently turn into "everything is effect". The loader fetches tools per server with `get_tools(server_name=)` and applies that server's policy; `merge_tool_specs` (core/tools_spec.py) **rejects duplicate tool names at startup** across built-in tools and MCP servers (the graph looks tools up by name, so a duplicate would silently override the approval requirement). v0.1 is `stdio` only (other transports in v0.2). The app loads and merges MCP tools at startup only when `MCP_SERVERS_PATH` (§8, optional) is set. The real client opens a session per call (this version has no context manager), so there is no lifetime for the loader to hold.
- Implementation note (audit 001): per-server `get_tools` has a 30-second startup timeout (`load_mcp_tool_specs(startup_timeout_s=)`). On timeout or server failure, **startup fails** with an `McpConfigError` carrying the server name and command (so a hung server cannot hold app startup indefinitely). The failure reason carries only the first line via `describe_error`.

## 7. Observability (T8)

- usage accounting: token totals from the model → state usage → exposed in the `usage` SSE event and the state endpoint.
- Structured logs (JSON): thread_id, node, tool, duration.
- `LANGSMITH_TRACING=true` enables LangSmith tracing (optional, off by default).
- Implementation note (T8): instead of hooking a callback onto the model, the agent node reads the response AIMessage's `usage_metadata` (LangChain's common field; the same value a callback would see) and accumulates it with a pure function — determined by node input alone and unit-testable. A response without usage_metadata still increments `calls`. Logs carry structured fields via `extra=` on the `lang_ai_agent.graph` logger, and `JsonFormatter` in `adapters/observability.py` renders one JSON line (installed only at the `make dev` entry point; tests read the same records through caplog). The clock behind `duration_ms` is injectable via `build_graph(clock=)` (tests use FixedClock). LangSmith reads the `LANGSMITH_TRACING` environment variable itself, but pydantic-settings does not export `.env` to `os.environ`, so at startup the Settings value is written back to `os.environ` to make `.env` alone sufficient.
- Implementation note (audit 001): the node emits the increment of one call (`usage_of_call`) and the state's `add_usage` reducer sums (§2). A tool failure is logged as the same `tool` record at WARNING with `exc_info` and `error_type`, and the model only gets the `describe_error` result. A stream failure is logged to `lang_ai_agent.api` as `stream_failed` ERROR with `exc_info` and thread_id. The rule: strings visible to the client or the model never contain tracebacks, paths, or request bodies (AUD-007).

## 8. Environment variables (committed as .env.example)

```
ANTHROPIC_API_KEY=                  # one key matching MODEL's provider (§8.1 table)
MODEL=anthropic:claude-sonnet-4-5   # init_chat_model "provider:model" form
APP_BEARER_TOKEN=
CHECKPOINT_DB_PATH=./data/checkpoints.db
SEND_MODE=dry_run                   # dry_run | live — the effect adapter's second gate
LANGSMITH_TRACING=false
MCP_SERVERS_PATH=                   # optional: path to mcp_servers.json. Empty = built-in tools only (T9)
```

### 8.1 Onboarding — how an installer supplies their own provider key (T11)

This is a public package, so whoever installs it must be able to run it with **their own API key**. The key lives only in `.env` (CLAUDE.md guardrail 4) and arrives through the two paths below.

| Provider (`MODEL` prefix) | Key variable | Suggested default model | SDK (base dependency) |
|---|---|---|---|
| `anthropic` (default) | `ANTHROPIC_API_KEY` | `claude-sonnet-4-5` | langchain-anthropic |
| `openai` | `OPENAI_API_KEY` | `gpt-5` | langchain-openai |
| `xai` | `XAI_API_KEY` | `grok-4` | langchain-xai |
| `google_genai` | `GOOGLE_API_KEY` | `gemini-2.5-pro` | langchain-google-genai |

The single source of this table is `PROVIDERS` in `adapters/llm.py`. A provider outside the table (anything else `init_chat_model` supports) also works when written in `MODEL`, but the startup check (item 2 below) is skipped because it does not know which variable to inspect.

1. **`lang-ai-agent init`** (`cli.py`, console script): choose a provider → enter the key (`getpass` — never echoed or logged) → model (default suggested, Enter accepts) → `APP_BEARER_TOKEN` generated automatically (`secrets.token_urlsafe`) → write `.env` (mode 0600, `SEND_MODE=dry_run` fixed). An existing `.env` is not overwritten without `--force`. Interactive I/O is tested by injecting console functions.
2. **Startup check (fail fast)**: `create_default_app()` (= `make dev`, `lang-ai-agent serve`) first exports `.env` to `os.environ` (`load_dotenv` — pydantic-settings does not export, so provider SDKs could not see a key that lived only in `.env`; found in the first smoke). Then, if the key for `MODEL`'s provider is empty, it fails **at startup** with a `ConfigError` rather than on the first model call, and points at the fix (`lang-ai-agent init` or the `.env` entry). Settings validation failures such as a missing `APP_BEARER_TOKEN` are wrapped the same way.
3. **`lang-ai-agent serve [--host] [--port]`**: the entry point for PyPI installers (no Makefile). Runs `create_default_app()` under uvicorn; a `ConfigError` prints only its message to stderr, no stack trace, exit code 2.

## 9. Directory layout (target)

```
lang_ai_agent/
  CLAUDE.md  README.md  Makefile  pyproject.toml  .env.example  mcp_servers.json.example
  .github/workflows/ci.yml        # make check (T11)
  docs/  scripts/smoke.py         # scripts/smoke.py = thin entry wrapper around lang_ai_agent.smoke
  src/lang_ai_agent/{core,adapters,api}/  cli.py (init · serve · smoke)  smoke.py (real-model smoke logic)
  tests/{helpers,unit,component,e2e}/
```

## 10. Distribution (packaging → PyPI)

- **Build**: `uv build` produces sdist + wheel from the single `pyproject.toml`. The `version` field in `pyproject.toml` is the only source of the version and is tied to the git tag (`vX.Y.Z`) — the package's `__version__` reads that value through `importlib.metadata` (no hardcoding), and the workflow fails on a tag/version mismatch.
- **Metadata** (T13): the license is written the PEP 639 way, `license = "MIT"` + `license-files = ["LICENSE"]` (`License-Expression`, `License-File`). The `License :: OSI Approved` classifier repeats the same information and PyPI treats it as deprecated, so it is omitted. Classifiers: Development Status, Python 3.12 (only the version CI verifies), Framework :: FastAPI, Typing :: Typed; `[project.urls]` has Homepage/Repository/Issues. PyPI shows the README's mermaid as a code block only (GitHub renders it) — a static image can be added later if needed.
- **Pipeline**: verify on TestPyPI first (T13, the agent may proceed autonomously) → production PyPI (T14).
- **Auth**: PyPI Trusted Publishing (OIDC, GitHub Actions) — no long-lived API token is stored in the repo or in secrets. Settled by the user on 2026-09-05. The one-time human setup (accounts, pending publishers, GitHub Environments), tag/version rules and the release procedure are in `docs/RELEASE.md`.
- **Gate**: a production PyPI release runs only **after maintainer approval** (`docs/WORKFLOW.md` §4) — PyPI does not allow re-uploading a version once deleted, so this is an irreversible action on the level of `SEND_MODE=live`. Implemented as Required reviewers on the GitHub Environment `pypi`. TestPyPI also refuses re-uploads of the same version, but it is a test index that end users never see, so CI publishes there without approval. This gate concerns the maintainer who ships releases; it has nothing to do with the people who install the package, and installing or running the package never asks for approval from anyone.
- **License**: MIT (SPEC §7, settled 2026-09-05).
- **Console script** (T11): `[project.scripts] lang-ai-agent = "lang_ai_agent.cli:main"`. An installer runs `lang-ai-agent init` (onboarding, §8.1) → `lang-ai-agent serve`, with no Makefile. `lang-ai-agent smoke [--mcp]` is the same real-model smoke as `scripts/smoke.py` (= `make smoke`) — for humans only, costs API calls.
- **Out of scope**: npm (JS/TS client SDK) release needs a separate package in this repository and is a v0.1 non-goal (SPEC §3). This section must be updated before that work starts.

## 11. Operational limits and v0.2 carry-overs (audit 001, 2026-09-05)

Items from `docs/001_ADVERSARIAL_CODE_AUDIT.md` judged valid but not fully resolved within v0.1 scope. Each is listed in the v0.2 row of SPEC §6.

- **Unbounded growth of messages and checkpoints (AUD-004)**: v0.1 offers only `DELETE /threads/{id}`. A message window/summary for what is sent to the model (`langchain_core.messages.trim_messages` — must not split tool-call/result pairs, so naive truncation is not an option), checkpoint pruning and thread TTL, and DB size metrics are v0.2. Until then the operating rule is "delete finished threads".
- **Multi-process serialization (AUD-001/002)**: the lock in §5 is valid only within a process. When scaling out with PostgresSaver, add a DB lease (per-thread execution right) together with an effect idempotency key (`tool_call_id`, duplicates rejected in the sending adapter).
- **Dependency vulnerability scanning**: nothing like `pip-audit` is in CI yet (the audit report was produced offline and did not include it). A v0.2 CI item.
