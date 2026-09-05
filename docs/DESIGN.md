# DESIGN — lang_ai_agent v0.1

이 문서가 구현의 진실의 원천이다. 그래프 토폴로지·상태 스키마 변경은 이 문서 수정이 먼저다.

## 1. 아키텍처

```
클라이언트 (curl / 데모 스크립트 / 추후 프론트)
      │ HTTP + SSE (Bearer 인증)
      ▼
api/app.py (FastAPI — 조립만)
      │ ainvoke / astream_events / Command(resume)
      ▼
core/graph.py (StateGraph + checkpointer)
  agent ──(tool_calls?)──► route
    ▲                        ├─ safe_tools  ──► agent
    │                        └─ approval ──interrupt()──► [사람] ──Command──► effect_tools ──► agent
    └──(no tool_calls)──► END
      │
      ├ adapters/llm.py         init_chat_model → 기본 Claude (모델 불가지론)
      ├ adapters/checkpoint.py  InMemorySaver(test) / AsyncSqliteSaver(dev·prod v0.1)
      ├ adapters/mcp_loader.py  mcp_servers.json → MultiServerMCPClient → BaseTool[]
      └ adapters/effects.py     발송 등 부작용 실제 구현 (SEND_MODE 게이트)
```

핵심 불변식: **effect 도구로 가는 유일한 엣지는 approval 노드를 지난다.** 이 토폴로지는 테스트로 고정된다 (TESTING §4).

## 2. 상태 (core/state.py)

```python
class PendingAction(BaseModel):
    tool_call_id: str; tool_name: str
    args_preview: dict[str, JsonValue]   # 사람에게 보여줄 요약 (대용량 금지)

class Usage(BaseModel):
    input_tokens: int = 0; output_tokens: int = 0; calls: int = 0

class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    pending: PendingAction | None
    usage: Usage
```

원칙: 상태는 매 체크포인트마다 직렬화된다 — 메시지와 최소 메타만. 도구의 큰 결과는 요약해 ToolMessage에 담고 원본은 상태 밖에 둔다.

## 3. 그래프 (core/graph.py)

- `agent` 노드: `llm.bind_tools(all_tools)` 호출 → AIMessage. tool_calls 없으면 END.
- `route` (conditional edge): tool_calls를 safe/effect로 분류. 하나의 응답에 섞여 있으면 safe 먼저 실행 후 effect 승인 순서.
- `safe_tools` 노드: 병렬 실행 허용, 결과 ToolMessage 추가 → agent 복귀.
- `approval` 노드: `pending` 기록 후 `interrupt({"action": pending, "draft": ...})` 호출 — 여기서 그래프가 저장·정지한다. 재개 시 `Command(resume={"approved": bool, "comment": str|None})`의 값이 interrupt() 반환값이 된다.
  - approved → `effect_tools` 실행 (SEND_MODE 게이트는 effects 어댑터 내부에서 한 번 더).
  - rejected → 거절 사유를 ToolMessage로 추가하고 agent 복귀 (초안 수정 or 종료는 모델 판단).
- `effect_tools` 노드: 순차 실행, 결과 ToolMessage → agent 복귀.
- 컴파일: `graph.compile(checkpointer=...)`. thread 단위 config `{"configurable": {"thread_id": ...}}`.

## 4. 도구 분류 규약 (core/tools_spec.py)

```python
class ToolSpec(BaseModel):
    tool: BaseTool
    requires_approval: bool
```

- v0.1 내장(페이크·retail-mcp 스키마 미러): `check_stockout`(safe), `get_reorder_suggestions`(safe), `send_reorder_email`(effect).
- MCP 로더 도구의 승인 여부는 `mcp_servers.json`의 서버·도구별 `requires_approval` 설정으로 매핑. **설정에 없는 도구는 기본 effect(승인 필요)로 취급** — 안전 기본값.

## 5. HTTP API (api/)

| 메서드·경로 | 입력 | 동작 |
|---|---|---|
| `POST /threads` | — | thread_id 발급 |
| `POST /threads/{id}/messages` | `{content}` | 그래프 실행, **SSE 스트림** 반환 |
| `GET /threads/{id}/state` | — | 메시지 요약·pending·usage |
| `POST /threads/{id}/approve` | `{approved, comment?}` | `Command(resume=...)`로 재개, SSE 스트림 반환 |

- 인증: `Authorization: Bearer $APP_BEARER_TOKEN` (v0.1 단일 토큰).
- SSE 이벤트 타입 (api/sse.py — `astream_events` 매핑): `token`(모델 텍스트 델타) · `tool_start`/`tool_end`(이름·소요) · `interrupt`(pending·초안) · `usage` · `done` · `error`. 이벤트 스키마는 Pydantic으로 고정하고 테스트한다.
- 구현 메모(T6): `astream_events`는 그래프 인터럽트를 이벤트로 직접 노출하지 않는다 — 스트림이 자연 종료된 후 `aget_state(config).interrupts`를 별도로 확인해 `interrupt` 이벤트를 만든다. `tool_start`/`tool_end`의 상관관계 id는 모델의 실제 tool_call.id가 아니라 `astream_events`의 run_id(on_tool_start 시점엔 전자를 알 수 없어 후자로 대체).
- 구현 메모(T7): `POST /threads`는 id만 발급하고, 스레드는 첫 `/messages`가 체크포인터에 첫 체크포인트를 쓸 때 생긴다 — "존재하지 않는 thread"는 `aget_state().values`가 비어 있는 경우(404). 대기 중 인터럽트가 없는 스레드의 `/approve`는 409. SSE 와이어 포맷은 `event:`에 이벤트 type, `data:`에 이벤트 JSON(sse-starlette). 앱은 `create_app(graph_factory, bearer_token)`으로 조립하며 체크포인터(async context manager)는 lifespan이 앱 수명 동안 연다; `make dev`는 `create_default_app()`을 uvicorn `--factory`로 띄워 import 시점엔 환경(.env → pydantic-settings)을 읽지 않는다.
- 구현 메모(T11, 실모델 스모크에서 발견): Anthropic 등 실제 모델은 메시지·청크의 `content`를 `str`이 아니라 **콘텐츠 블록 리스트**(`[{"type": "text", "text": ...}]`)로 준다. `api/sse.py`의 `content_text()`가 두 형태 모두에서 텍스트만 뽑아 `token` 이벤트와 `/state`의 `last_message`를 만든다(`text` 블록만 취급, `tool_use` 블록은 무시). ScriptedChatModel은 `str`만 내므로 블록 리스트 대본 테스트로 회귀를 고정한다 — 이걸 놓쳐 1차 스모크는 token 이벤트가 0건이었다.

## 6. MCP 로더 (adapters/mcp_loader.py)

`mcp_servers.json` (예시는 `.example`로 커밋):

```json
{
  "retail": {
    "command": "npx", "args": ["tsx", "/path/to/retail-mcp/src/server.ts"],
    "transport": "stdio",
    "approval": { "default": "effect", "safe": ["sell_through", "inventory_status", "stockout_risk", "sync_status"] }
  }
}
```

로더는 이 설정을 파싱해 `MultiServerMCPClient`로 도구를 로드하고 ToolSpec에 매핑한다. **단위 테스트는 파싱·매핑까지만**(실 프로세스 없음), 실 연결은 smoke에서.

- 구현 메모(T9): `approval`은 `{default: "safe"|"effect" (기본 effect), safe: [...], effect: [...]}` — 두 목록에 같은 도구가 있으면 설정 오류. 서버 항목은 `extra="forbid"`로 파싱해 키 오타가 조용히 "전부 effect"로 둔갑하지 않게 한다. 로더는 서버별 `get_tools(server_name=)`로 도구를 받아 그 서버의 정책을 적용하고, `merge_tool_specs`(core/tools_spec.py)가 내장 도구·MCP 서버 간 **도구명 중복을 기동 시점에 거부**한다(그래프가 이름으로 도구를 찾으므로 중복은 승인 요구를 조용히 덮어쓴다). v0.1은 `stdio` 전용(다른 transport는 v0.2). 앱은 `MCP_SERVERS_PATH`(§8, 선택)가 설정된 경우에만 기동 시 로드해 내장 도구와 병합한다. 실 클라이언트는 호출마다 세션을 열므로(이 버전은 컨텍스트 매니저 미지원) 로더가 붙잡을 수명은 없다.

## 7. 관측성 (T8)

- usage 집계: 모델 콜백에서 토큰 합산 → 상태 usage 갱신 → `usage` SSE 이벤트·state 조회에 노출.
- 구조화 로그(JSON): thread_id, node, tool, duration. 
- `LANGSMITH_TRACING=true`면 LangSmith 트레이싱 활성 (옵션, 기본 off).
- 구현 메모(T8): 토큰 합산은 모델에 콜백을 거는 대신 agent 노드가 응답 AIMessage의 `usage_metadata`(LangChain 공통 필드, 콜백이 보는 값과 동일)를 읽어 순수 함수 `usage_after_call`로 누적한다 — 노드 입력만으로 결정되고 단위 테스트 가능. usage_metadata가 없는 응답도 `calls`는 +1. 로그는 `lang_ai_agent.graph` 로거에 `extra=`로 구조화 필드를 싣고 `adapters/observability.py`의 JsonFormatter가 한 줄 JSON으로 렌더링(`make dev` 진입점에서만 설치, 테스트는 caplog로 같은 레코드를 읽음). `duration_ms`의 시계는 `build_graph(clock=)`로 주입 가능(테스트는 FixedClock). LangSmith는 `LANGSMITH_TRACING` 환경변수를 스스로 읽지만 pydantic-settings는 `.env`를 `os.environ`으로 내보내지 않으므로, 기동 시 Settings 값을 `os.environ`에 다시 써서 `.env`만으로도 켜지게 배선한다.

## 8. 환경변수 (.env.example로 커밋)

```
ANTHROPIC_API_KEY=                  # MODEL의 프로바이더에 맞는 키 하나 (§8.1 표)
MODEL=anthropic:claude-sonnet-4-5   # init_chat_model "provider:model" 형식
APP_BEARER_TOKEN=
CHECKPOINT_DB_PATH=./data/checkpoints.db
SEND_MODE=dry_run                   # dry_run | live — effect 어댑터의 2차 게이트
LANGSMITH_TRACING=false
MCP_SERVERS_PATH=                   # 선택: mcp_servers.json 경로. 비우면 내장 도구만 (T9)
```

### 8.1 온보딩 — 설치자가 자기 프로바이더 키를 넣는 경로 (T11)

공개 패키지이므로 설치한 사람이 누구든 **자기 API 키**로 돌릴 수 있어야 한다. 키는 `.env`에만 존재하며(CLAUDE.md 가드레일 4) 아래 두 갈래로 들어온다.

| 프로바이더(`MODEL` 접두) | 키 환경변수 | 기본 제안 모델 | SDK(기본 의존성) |
|---|---|---|---|
| `anthropic` (기본) | `ANTHROPIC_API_KEY` | `claude-sonnet-4-5` | langchain-anthropic |
| `openai` | `OPENAI_API_KEY` | `gpt-5` | langchain-openai |
| `xai` | `XAI_API_KEY` | `grok-4` | langchain-xai |
| `google_genai` | `GOOGLE_API_KEY` | `gemini-2.5-pro` | langchain-google-genai |

이 표의 단일 소스는 `adapters/llm.py`의 `PROVIDERS`다. 표에 없는 프로바이더(`init_chat_model`이 지원하는 다른 것)도 `MODEL`에 적으면 동작하지만, 기동 검사(아래 2)는 어느 환경변수를 봐야 할지 몰라 건너뛴다.

1. **`lang-ai-agent init`** (`cli.py`, 콘솔 스크립트): 프로바이더 선택 → 키 입력(`getpass` — 화면·로그에 남지 않음) → 모델(기본 제안, Enter로 수락) → `APP_BEARER_TOKEN` 자동 생성(`secrets.token_urlsafe`) → `.env` 작성(권한 0600, `SEND_MODE=dry_run` 고정). 기존 `.env`는 `--force` 없이는 덮어쓰지 않는다. 대화형 입출력은 콘솔 함수 주입으로 테스트한다.
2. **기동 검사(fail-fast)**: `create_default_app()`(= `make dev`, `lang-ai-agent serve`)은 먼저 `.env`를 `os.environ`으로 내보낸다(`load_dotenv` — pydantic-settings는 내보내지 않아 프로바이더 SDK가 `.env`만의 키를 못 봤음, 1차 스모크에서 발견). 그 다음 `MODEL`의 프로바이더 키가 비어 있으면 첫 모델 호출이 아니라 **기동 시점에** `ConfigError`로 실패하고 수정 방법(`lang-ai-agent init` 또는 `.env` 항목)을 안내한다. `APP_BEARER_TOKEN` 누락 같은 Settings 검증 실패도 같은 경로로 감싼다.
3. **`lang-ai-agent serve [--host] [--port]`**: PyPI 설치자의 실행 진입점(Makefile 없이). `create_default_app()`을 uvicorn으로 띄우며 `ConfigError`는 스택트레이스 없이 메시지만 stderr에 내고 종료 코드 2.

## 9. 디렉터리 구조 (목표)

```
lang_ai_agent/
  CLAUDE.md  README.md  Makefile  pyproject.toml  .env.example  mcp_servers.json.example
  .github/workflows/ci.yml        # make check (T11)
  docs/  scripts/smoke.py         # scripts/smoke.py = lang_ai_agent.smoke의 얇은 진입 래퍼
  src/lang_ai_agent/{core,adapters,api}/  cli.py(init·serve·smoke)  smoke.py(실모델 스모크 로직)
  tests/{helpers,unit,component,e2e}/
```

## 10. 배포 (Packaging → PyPI)

- **빌드**: `uv build`로 `pyproject.toml` 하나(single source)에서 sdist + wheel 생성. 버전은 `pyproject.toml`의 `version` 필드가 유일한 소스이며 git 태그(`vX.Y.Z`)와 연동한다.
- **파이프라인**: TestPyPI로 먼저 검증(T13, 에이전트가 자율 진행 가능) → 정식 PyPI(T14).
- **인증**: PyPI Trusted Publishing(OIDC, GitHub Actions) 사용 — 장기 API 토큰을 레포·시크릿에 저장하지 않는다.
- **게이트**: 정식(prod) PyPI 배포 실행은 **사람 승인 후 트리거**한다(`docs/WORKFLOW.md` §4) — PyPI는 동일 버전 삭제 후 재업로드가 불가능해 `SEND_MODE=live`급 비가역 행동이기 때문. TestPyPI는 이 제약이 없어 CI 자동 배포 대상이 될 수 있다.
- **콘솔 스크립트**(T11): `[project.scripts] lang-ai-agent = "lang_ai_agent.cli:main"`. 설치자는 `lang-ai-agent init`(온보딩, §8.1) → `lang-ai-agent serve`로 Makefile 없이 기동한다. `lang-ai-agent smoke [--mcp]`는 `scripts/smoke.py`(= `make smoke`)와 같은 실모델 스모크(사람 전용, API 비용).
- **범위 밖**: npm(JS/TS 클라이언트 SDK) 배포는 이 저장소의 별도 패키지가 필요한 작업으로, v0.1 비목표(SPEC §3)다. 착수 시 이 섹션 갱신이 선행되어야 한다.
