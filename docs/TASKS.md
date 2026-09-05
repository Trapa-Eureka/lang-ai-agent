# TASKS — lang_ai_agent v0.1 백로그

## 사용법

- 한 에이전트 세션 = 한 태스크. 프롬프트 템플릿:
  > `docs/SPEC.md`, `docs/DESIGN.md`, `docs/TESTING.md`를 읽고 **T5**를 수행해. 완료 기준을 전부 충족하고 `make check`가 통과할 때까지 스스로 수정해. 끝나면 변경 파일과 검증 결과를 요약해.
- 완료 기준은 전부 기계 판정 가능. 완료 시 상태 `DONE(날짜)` + 커밋(`T{n}: 요약`).
- 병렬 레인: T1 완료 후 **A(T2), B(T3), C(T4)** 는 서로 다른 worktree 에이전트로 동시 진행 가능. T5가 허브, 이후 **T6/T8/T9** 재병렬.

의존 그래프: `T0 → T1 → {A: T2, B: T3, C: T4} → T5 → {T6, T8, T9} → T7(T5,T6) → T10(T7,T8,T9) → T11 → T16 → T13 → T14 → T15`. T12(문서)는 선행 의존 없음 — 독립 실행, 완료(DONE). T16(코드 감사 001 대응)은 사용자 코드 검수 결과로 T11 뒤·T13 앞에 삽입.

---

### T0 — 프로젝트 스캐폴딩 · 상태: DONE(2026-09-04)
- 목표: uv 프로젝트(pyproject), ruff·pyright(strict)·pytest(+asyncio, cov) 설정, Makefile(check/test/lint/typecheck/dev/smoke), `.env.example`, `.gitignore`(.env, data/).
- 완료 기준: [x] `make check` 통과 [x] 더미 async 테스트 1개 실행 [x] pyright strict 확인(의도적 타입 오류가 잡히는 스냅샷 테스트) [x] git init + 첫 커밋

### T1 — 상태·타입·도구 규약 · 상태: DONE(2026-09-04) · 의존: T0
- 목표: `core/state.py`(AgentState, PendingAction, Usage), `core/tools_spec.py`(ToolSpec, safe/effect 분류), SSE 이벤트 Pydantic 스키마(`api/sse.py`의 타입부).
- 완료 기준: [x] 전 모델 pyright 통과·직렬화 라운드트립 테스트 [x] 상태 대용량 금지 규칙을 docstring에 명시 [x] check 통과

### T2 (레인 A) — ScriptedChatModel + 대본 빌더 · 상태: DONE(2026-09-04) · 의존: T1
- 목표: TESTING §2의 ScriptedChatModel(BaseChatModel 상속, tool_calls 재생, assert_exhausted)과 `script()` 빌더.
- 완료 기준: [x] 대본 소진·잔여·불일치 시 명확한 실패 메시지 테스트 [x] tool_calls 포함 AIMessage 재생 테스트 [x] check 통과

### T3 (레인 B) — 도구 계층 · 상태: DONE(2026-09-04) · 의존: T1
- 목표: 내장 페이크 3종(retail-mcp 스키마 미러, `fail_on` 주입), `adapters/effects.py`(SEND_MODE 이중 게이트) + MockEffects.
- 완료 기준: [x] 도구 인자 zod급 검증(Pydantic args_schema) [x] dry_run에서 실발송 경로 미진입 테스트 [x] check 통과

### T4 (레인 C) — 체크포인터·모델 팩토리 · 상태: DONE(2026-09-04) · 의존: T1
- 목표: `adapters/checkpoint.py`(InMemory/Sqlite 선택), `adapters/llm.py`(init_chat_model, MODEL env), thread config 유틸.
- 완료 기준: [x] 임시파일 AsyncSqliteSaver 저장·복원 테스트 [x] 잘못된 MODEL 문자열 → 수정 방법 담긴 에러 [x] check 통과
- 개정(T5 작업 중 발견): 그래프가 async로 동작해 동기 `SqliteSaver`는 `ainvoke`/`aget_state`에서 `NotImplementedError`를 던짐 — `AsyncSqliteSaver`로 교체(아래 §체크포인터 표기도 동일 수정).

### T5 — 그래프 코어 · 상태: DONE(2026-09-04) · 의존: T2, T3, T4
- 목표: `core/graph.py` — DESIGN §3 토폴로지(agent/route/safe_tools/approval+interrupt/effect_tools), 컴파일 팩토리.
- 완료 기준: [x] **TESTING §3 골든 궤적 4종 전부** [x] **§4 그래프·승인 게이트 5항목 전부**(구조 불변식 포함) [x] check 통과
- 덤: TESTING §4 "도구·에러"의 미등록 도구·도구 예외 처리도 함께 구현·테스트(그래프 자체 구현에서 분리 불가능해서 포함, T5 완료 기준엔 없었지만 커버).
- 개정: T4의 체크포인터를 `AsyncSqliteSaver`로 교정(위 T4 항목 참고) + `PendingAction`을 msgpack 역직렬화 허용 목록에 등록(등록 안 하면 "will be blocked in a future version" 경고 후 향후 langgraph 버전에서 재시작 복원이 깨질 수 있었음).

### T6 — SSE 이벤트 매퍼 · 상태: DONE(2026-09-04) · 의존: T5
- 목표: `astream_events` → 내부 이벤트 스트림 변환(api/sse.py), 이벤트 순서 보장.
- 완료 기준: [x] 대본 기반 스트림에서 이벤트 순서·스키마 테스트 [x] interrupt 이벤트에 pending 포함 [x] check 통과
- 개정: (1) ScriptedChatModel(T2)에 `_stream`/`_astream` 추가 — 스트리밍 미구현 모델은 `on_chat_model_stream`을 아예 안 내보내 token 이벤트 매핑을 테스트할 수 없었음. (2) `Usage`도 msgpack 허용 목록에 추가(T5에서 `PendingAction`만 등록해 `Usage`가 조용히 차단되고 있었음) — 인터럽트 상태 조회(`aget_state`) 과정에서 발견.
- 설계 메모: `astream_events`는 그래프 인터럽트를 이벤트로 직접 노출하지 않음 — 스트림 종료 후 `aget_state(config).interrupts`로 별도 확인. `tool_call_id`는 모델의 실제 tool_call.id가 아니라 `astream_events`의 run_id(on_tool_start 시점엔 전자를 알 수 없음).

### T7 — FastAPI 서비스 · 상태: DONE(2026-09-05) · 의존: T5, T6
- 목표: 엔드포인트 4종 + Bearer 인증 + SSE 응답. app.py는 조립만(로직 없음).
- 완료 기준: [x] **TESTING §4 API·SSE 4항목 전부** (httpx ASGI) [x] `make dev` 기동 [x] check 통과
- 설계 메모: `create_app(graph_factory, bearer_token)`(주입용, 테스트) / `create_default_app()`(`.env` → Settings → 실모델 + AsyncSqliteSaver). `make dev`는 uvicorn `--factory`로 후자를 호출해 import 시점에 환경을 읽지 않음. 스레드는 첫 `/messages`에서 체크포인터에 생기며 존재 여부는 `aget_state().values`로 판정. 대기 중 인터럽트 없는 스레드의 `/approve`는 409. 설정은 pydantic-settings, SSE 프레이밍은 sse-starlette(`event:`=이벤트 type, `data:`=JSON).

### T8 — usage·관측성 · 상태: DONE(2026-09-05) · 의존: T5
- 목표: 토큰 집계 콜백 → 상태 usage → SSE/state 노출, 구조화 JSON 로그, LANGSMITH_TRACING 옵션 배선.
- 완료 기준: [x] usage 누적 일치 테스트(TESTING §4) [x] 로그에 thread_id·node·tool 포함 스냅샷 [x] check 통과
- 설계 메모: 토큰 합산은 콜백 대신 agent 노드가 응답의 `usage_metadata`를 순수 함수 `usage_after_call`로 누적(DESIGN §7 메모). 로그 시계는 `build_graph(clock=)`로 주입 — 테스트는 `tests/helpers/fixed_clock.py`로 정확한 duration_ms 스냅샷. ScriptedChatModel(T2)은 턴별 고정 usage(`input_tokens=`/`output_tokens=`)를 받고 스트리밍 시 **마지막 청크에만** usage_metadata를 실음(LangChain이 청크 usage를 합산하므로 전 청크에 붙이면 배수가 됨 — 실측). LangSmith는 `LANGSMITH_TRACING`을 직접 읽지만 pydantic-settings는 `.env`를 `os.environ`에 안 내보내므로 기동 시 다시 써주는 게 "배선".

### T9 — MCP 로더 · 상태: DONE(2026-09-05) · 의존: T5
- 목표: `mcp_servers.json` 파서 + `MultiServerMCPClient` 로더 + approval 매핑(미지정 도구 → effect 기본값), `.example` 파일.
- 완료 기준: [x] 파싱·매핑 단위 테스트(실 프로세스 없음) [x] 설정 파일 부재 시 수정 방법 담긴 에러 [x] check 통과
- 설계 메모: 파싱(Pydantic, `extra="forbid"` — `aproval` 같은 오타가 조용히 "전부 effect"로 둔갑하지 않게) / 매핑(순수) / 연결(`load_mcp_tool_specs`, 클라이언트 팩토리 주입 가능 → 테스트는 페이크)의 3층. 서버별 `get_tools(server_name=)`로 정책을 정확히 적용. `core/tools_spec.merge_tool_specs`가 내장·MCP·서버 간 **도구명 중복을 기동 시점에 거부**(그래프가 이름으로 도구를 찾아서 나중 것이 앞 것의 승인 요구를 조용히 덮어쓰기 때문). v0.1은 stdio 전용. 앱 배선: `MCP_SERVERS_PATH`(선택, DESIGN §8에 추가) 설정 시 기동 때 로드·병합 — T11 smoke `--mcp`의 진입점. 이 버전의 `MultiServerMCPClient`는 컨텍스트 매니저가 제거되어(호출마다 세션) 로더가 붙잡을 수명이 없음.

### T10 — e2e-mock + 커버리지 · 상태: DONE(2026-09-05) · 의존: T7, T8, T9
- 목표: SPEC §4 시나리오 1~4를 API 레벨 e2e-mock으로(재시작 내성 포함), 커버리지 리포트.
- 완료 기준: [x] 4개 시나리오 전부 통과 [x] core ≥ 90% 리포트 첨부 [x] check 통과
- 시나리오(`tests/e2e/test_scenarios.py`, httpx ASGI로 실제 앱 관통): 1 조회(인터럽트 0, usage 합계) · 2 승인 발송(safe 조회 → 인터럽트에 초안·수신자 → /approve → 발송 → 결과 보고, dry_run 2차 게이트) · 3a 거절→정중한 종료 · 3b 거절 코멘트→**수정 초안 재제시(2차 인터럽트)**→승인→v2만 발송 · 4 재시작 내성(임시 SQLite: 앱·그래프·체크포인터 완전 폐기 후 새 앱에서 같은 thread_id로 /state 복원·/approve 발송) · 스레드 격리(TESTING §4).
- 커버리지 리포트(첨부): `make test`가 `coverage report --fail-under=90 --include='src/lang_ai_agent/core/*'`로 게이트. 결과 — core/graph.py 130 stmts/18 br 100%, core/state.py 16/0 100%, core/tools_spec.py 14/2 100%, **core 합계 160 stmts/20 br, miss 0 → 100%**; 프로젝트 전체 546 stmts/68 br 100%, 139 passed.

### T11 — 스모크 + 포트폴리오 준비 · 상태: DONE(2026-09-05) · 의존: T10
- 목표: `scripts/smoke.py`(실모델 시나리오1 + 콘솔 승인, `--mcp`로 실 retail-mcp), 영어 README 초안(내부 docs는 한국어 유지), 60초 데모 스크립트 시나리오, GitHub Actions `ci.yml`(make check).
- 확장(사용자 확인 하에): 셀프서비스 온보딩(SPEC 목표 8, DESIGN §8.1) — `lang-ai-agent init/serve/smoke` 콘솔 스크립트, 기동 시 프로바이더 키 검사(`ConfigError`), OpenAI/xAI/Google SDK를 기본 의존성으로.
- 완료 기준: [x] smoke 절차가 README에 5줄 이내(3줄) [x] ci.yml 문법 검증 통과(YAML 파싱; act 미설치) [x] 데모 시나리오 문서화(`docs/DEMO.md`) [x] check 통과(176 passed, core 100%, 전체 100%)
- 실모델 스모크 결과(사용자 키, `anthropic:claude-sonnet-4-5`, dry_run, 과금 2회 ≈ $0.02 후 키 삭제): 시나리오 1 정상 — 도구 호출·token 스트리밍·usage(1회 ≈ 2.0k 입력/0.3k 출력 토큰, 8초). 스모크로만 드러난 결함 2건 수정: (1) pydantic-settings가 `.env`를 `os.environ`에 내보내지 않아 프로바이더 SDK가 키를 못 봄 → 기동 시 `load_dotenv`; (2) 실모델 content가 블록 리스트라 token 이벤트 0건·`last_message` None → `api/sse.py: content_text()`. 둘 다 회귀 테스트로 고정.
- 설계 메모: 스모크 로직은 `lang_ai_agent/smoke.py`(`run_scenarios`가 콘솔 콜백을 주입받아 대본 모델로 테스트됨), `scripts/smoke.py`는 래퍼. `run_smoke`는 `SEND_MODE`를 dry_run으로 강제하고 `--mcp` 없이는 MCP를 로드하지 않으며 `init`이 쓴 `.env`를 전제로 한다(환경 변수를 건드리지 않음). `init`은 `.env`를 0600으로 생성(`touch(mode=)` 후 기록 — 권한 창 없음), 기존 파일은 `--force` 필요, 키는 `getpass`로만. 프로바이더 표(`adapters/llm.py: PROVIDERS`)는 anthropic/openai/xai/google_genai — 표 밖 프로바이더는 기동 검사 생략. `serve`·`smoke`는 `create_default_app`/`open_default_graph`를 재사용(조립 중복 없음).
- 사용자 결정(2026-09-05, 머지 후): 기본 모델 `claude-sonnet-4-5` 유지(SPEC §8 해소), `init` 제안 모델명 유지, README 영문 톤은 에이전트가 최종 검토(표현 8곳 다듬음, 구조·주장 변경 없음).

### T12 — PyPI·npm 배포 방향 문서화 · 상태: DONE(2026-09-04) · 의존: 없음
- 목표: SPEC/DESIGN/WORKFLOW/README에 "PyPI 배포"를 v0.1 정식 목표로 반영하고, npm(JS/TS 클라이언트 SDK) 배포는 v0.1 비목표로 명시해 착수를 보류한다. 사용자와의 대화로 방향 확정.
- 완료 기준: [x] SPEC §2/§3/§5/§6/§7 갱신 [x] DESIGN에 §10 배포(Packaging) 섹션 추가 [x] WORKFLOW §4 자율성 한계선에 "정식 PyPI 배포 승인" 추가 [x] README 상태 로그 갱신 [x] 코드 변경 없음(문서 전용) 확인

### T13 — PyPI 패키징 · 상태: DONE(2026-09-05) · 의존: T11, T16
- 목표: `pyproject.toml` 배포 메타데이터(description/license/classifiers/urls/authors) 정비, `uv build`로 sdist+wheel 생성 검증, TestPyPI 시험 배포. 콘솔 스크립트(`[project.scripts] lang-ai-agent`)는 T11에서 선반영됨 — 설치 후 `lang-ai-agent init`이 동작하는지 TestPyPI 설치 확인에 포함.
- 선행 결정(2026-09-05, 문서 선행 완료 — `docs/RELEASE.md`): 라이선스 **MIT** → `LICENSE` 파일 추가를 T15에서 T13으로 당김. 인증은 **Trusted Publishing** → `.github/workflows/publish.yml`(build + `testpypi` job)을 T13에서 작성. TestPyPI 업로드는 사람이 RELEASE.md §1(계정·pending publisher·GitHub Environments)을 마친 뒤 `v0.1.0rcN` 태그로 수행. `uv build`는 2026-09-05 현재 상태로 이미 성공(누락은 METADATA의 License/Classifier/Project-URL/Keywords뿐), `src/` 코드 변경 없음.
- 완료 기준: [x] `uv build` 성공 산출물(sdist+wheel) 확인 [x] `LICENSE`(MIT)·메타데이터가 wheel METADATA에 반영(`License-Expression: MIT`, `License-File`, classifiers, Project-URL, Keywords; LICENSE는 sdist·wheel 모두 포함) [x] `publish.yml` 문법 검증(YAML 파싱, build + `testpypi` job) [x] TestPyPI 업로드 성공(태그 `v0.1.0rc1` → Publish run 33956057465, build·testpypi job 모두 success — Trusted Publishing 첫 실행에 정상) [x] `--index-url` TestPyPI로 격리 venv에 설치·`lang-ai-agent --help`·버전 `0.1.0rc1`·`License-Expression: MIT` 확인, 프로젝트 페이지에 wheel+sdist 등록 [x] check 통과(208 passed, 100%)
- 구현 메모: 버전은 `uv version 0.1.0rc1`로 pyproject와 uv.lock을 함께 올렸고(정식 `0.1.0`은 T14), `__version__`은 `importlib.metadata`로 읽어 하드코딩을 없앴다(스캐폴딩 테스트는 pyproject 값과 일치 검사). 빌드된 wheel은 프로젝트 밖 격리 환경(`uv run --isolated --no-project --with dist/*.whl`)에서 콘솔 스크립트·버전을 확인. `publish.yml`의 build job은 태그↔버전 일치 검사 → `make check` → `uv build` → wheel 스모크 → 아티팩트 업로드, `testpypi` job은 환경 `testpypi` + `id-token: write`로 `pypa/gh-action-pypi-publish`. 라이선스 classifier는 PEP 639 표현식과 중복이라 넣지 않았다(DESIGN §10).

### T14 — PyPI 정식 배포 워크플로 · 상태: TODO · 의존: T13
- 목표: GitHub Actions + Trusted Publishing(OIDC)으로 태그 푸시 시 정식 PyPI 배포 파이프라인 구성. **정식 배포 실행은 매번 사람 승인 후 트리거**(WORKFLOW §4). 구현은 T13의 `publish.yml`에 `pypi` job 추가(환경 `pypi`의 Required reviewers = 사람 승인, 정식 태그에만 실행). 태그·버전 규칙은 `docs/RELEASE.md` §3에 선문서화(2026-09-05).
- 완료 기준: [ ] 워크플로 yml 문법 검증 [x] 버전 태그 규칙 문서화(RELEASE.md §3) [ ] 사람 승인 하 정식 배포 1회 성공 [ ] check 통과

### T15 — 최종 통합 공개 · 상태: TODO · 의존: T11, T14
- 목표: 루트 README에 PyPI 배지·설치 커맨드 추가, GitHub 공개 체크리스트(SPEC §7) 전항목 점검. LICENSE는 MIT로 확정되어 T13에서 추가되므로 여기서는 존재·저작권자 표기만 확인.
- 완료 기준: [ ] README에 PyPI 배지 + `pip install`/`uv add` 안내 [ ] LICENSE(MIT) 존재·저작권자 확인 [ ] SPEC §7 기준 전항목 충족 확인

### T16 — 코드 감사 001 대응 · 상태: DONE(2026-09-05) · 의존: T11 (T13 전 수행)
- 목표: 사용자 코드 검수 보고서 `docs/001_ADVERSARIAL_CODE_AUDIT.md`(9건 + 추가 2건)를 코드로 재검증하고 타당한 항목을 수정, 남는 항목은 v0.2로 문서화(DESIGN §11).
- 완료 기준: [x] 11건 전부 판정·조치(보고서 §5 표) [x] 회귀 테스트(TESTING §4 "감사 001 대응") [x] DESIGN §2/§3/§5/§6/§7/§11·SPEC §6·CLAUDE.md 반영 [x] check 통과(208 passed, 라인·분기 커버리지 100%, 경쟁 조건 테스트 5회 반복 안정)
- 판정: 9건 전부 타당 — AUD-001/002(스레드별 직렬화 + 승인 대기 중 `/messages` 409), 003(`add_usage` 리듀서), 004(부분: `DELETE /threads/{id}`, 나머지 v0.2), 005(요청 한도·preview 요약), 006(SSE 예외 경계), 007(`describe_error`), 008(`compare_digest`), 009(원자적 `.env`). 추가 2건: MCP 기동 타임아웃 수정, `POST /threads` 미저장은 의도된 설계로 DESIGN §5에 명시.
- 감사가 놓친 파생 결함: `effect_tools`가 원본 tool_call 대신 `pending.args_preview`를 실행 인자로 쓰고 있었다 — preview 요약과 함께 원본 재조회로 수정(DESIGN §2/§3).
- 설계 메모: 락은 스트림 생성기 안에서 잡아 응답 수명과 같이 풀리고, 핸들러의 락 밖 검사(HTTP 상태)와 락 안 재검사(`error` 이벤트)를 분리했다. 본문 한도는 순수 ASGI 미들웨어(BaseHTTPMiddleware는 SSE 응답을 감싸 끊김 감지를 방해). `usage_after_call`은 `usage_of_call`(증분)로 대체.

---

## 보류 — npm(JS/TS 클라이언트 SDK) 배포

- 2026-09-04: 사용자 확인 하에 착수 보류(T12). 이 백엔드의 HTTP+SSE API(DESIGN §5)를 감싸는 별도 TypeScript 클라이언트 패키지를 신규로 만들어 npm에 배포하는 방향이며, PyPI 배포(T13~T15) 안정화 이후 별도로 재논의한다.
- 재개 시 예상 범위(초안, 번호는 재개 시점에 새로 부여): SDK 스캐폴딩(`clients/typescript/` 등 위치 결정) → 코어 클라이언트 구현(DESIGN §5 계약 타입화 + 목 서버 기반 유닛 테스트) → npm 배포 워크플로.

## v0.2 대기열 (착수 금지 — SPEC 로드맵 참조)

- PostgresSaver / supervisor 멀티에이전트 / 실 retail-mcp 상시 연결(프로세스 수명 관리) / 평가 하니스는 v0.3
