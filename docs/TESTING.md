# TESTING — lang_ai_agent

목적: **실 LLM 없이** 에이전트 그래프의 모든 경로를 로컬 결정론으로 검증한다. LLM 앱에서 Shift-Left의 핵심은 "모델을 대본으로 대체"하는 것 — 대본이 있으면 그래프는 그냥 상태 기계이고, 상태 기계는 완전하게 테스트된다.

## 1. 원칙

- 테스트에서 네트워크·실 LLM 호출 0건. 모델 = ScriptedChatModel, 도구 = 페이크, 발송 = 목, 체크포인터 = InMemorySaver(재시작 테스트만 임시파일 AsyncSqliteSaver — 그래프가 async라 동기 SqliteSaver는 쓸 수 없음).
- 플레이키 발생 시 **실모델을 넣어 고치지 않는다** — 대본이나 코드를 고친다.
- `make check` = ruff + pyright + pytest. 전체 수 초 내.
- 실모델 검증은 `make smoke`(사람 전용)와 v0.3 evals의 몫.

## 2. 테스트 헬퍼 (tests/helpers/)

| 헬퍼 | 내용 |
|---|---|
| `ScriptedChatModel` | BaseChatModel 상속. 생성자에 AIMessage 시퀀스(대본)를 받아 호출마다 순서대로 반환. tool_calls 포함 가능. **대본 소진·미소비 잔여 시 명확히 실패**(assert_exhausted) |
| `script()` 빌더 | `script().tool_call("check_stockout", {...}).final("본점 위험 3건...")` 식으로 대본을 읽기 쉽게 조립 |
| 페이크 도구 3종 | DESIGN §4 내장 도구 — 고정 응답 + `fail_on` 주입으로 도구 에러 재현 |
| `MockEffects` | send_reorder_email 실제 구현 대체 — 호출 기록, SEND_MODE 게이트 검증용 |
| `FixedClock` / 고정 usage | 시간·토큰 수치 결정론 |

## 3. 골든 궤적 (component — 그래프 레벨)

각 시나리오의 **노드 방문 순서**를 하드코딩해 회귀를 잡는다.

- 조회만: `agent → safe_tools → agent → END` (인터럽트 0회)
- 승인 발송: `agent → safe_tools → agent → approval(interrupt) ⏸ → [resume approved] → effect_tools → agent → END`
- 거절: `... → approval(interrupt) ⏸ → [resume rejected+comment] → agent → END`, effect 실행 0회
- 혼합 tool_calls(safe+effect 동시): safe 먼저 전부 실행 후 approval 진입

## 4. 필수 엣지 케이스 체크리스트

**그래프·승인 게이트**
- [x] 구조 불변식: 컴파일된 그래프에서 effect_tools로 들어오는 엣지가 approval 경유뿐임을 검증 (get_graph 순회)
- [x] 인터럽트 페이로드에 pending 요약·초안 포함, 대용량 원본 미포함
- [x] resume 값(`approved/comment`)이 interrupt() 반환값으로 정확히 전달
- [x] 거절 comment가 ToolMessage로 모델에 전달됨 (대본으로 후속 응답 검증)
- [x] SEND_MODE=dry_run이면 승인돼도 MockEffects가 실발송 경로를 타지 않음 (이중 게이트)

**영속·재시작**
- [x] 임시파일 AsyncSqliteSaver: 인터럽트 상태에서 그래프 객체 폐기 → 재컴파일(동일 DB·thread_id) → approve 재개 성공
- [x] 스레드 격리: 두 thread_id 병행 실행, 상태 혼입 없음

**도구·에러**
- [x] 도구 예외 → 에러 ToolMessage로 변환, 그래프는 죽지 않고 agent가 대본대로 사과·대안 제시
- [x] 미등록 도구 호출 대본 → 명확한 실패 메시지
- [x] MCP 로더: 설정 파싱·approval 매핑 단위 테스트 (설정 누락 도구 → effect 기본값)

**API·SSE (httpx ASGI)**
- [x] 인증 없음/틀림 → 401
- [x] messages 스트림 이벤트 순서: token* → tool_start/end* → (interrupt | usage → done)
- [x] interrupt 후 GET state에 pending 노출, approve 후 done까지 스트림
- [x] 존재하지 않는 thread_id → 수정 방법 담긴 404

**usage**
- [x] 다중 모델 호출 누적 집계가 대본의 고정 usage 합과 일치

**온보딩·설정 (T11)**
- [x] `lang-ai-agent init`: 주입한 콘솔 입력으로 `.env` 작성(권한 0600, 프로바이더 키·자동 생성 토큰 포함), 기존 `.env`는 `--force` 없이는 거부
- [x] 기동 검사: `MODEL` 프로바이더의 키 누락 → `create_default_app()`이 기동 시점에 `ConfigError`(환경변수명 + `lang-ai-agent init` 안내), 키 있으면 통과
- [x] SSE `token`·`/state`의 `last_message`가 실모델 형태(콘텐츠 블록 리스트)에서도 나옴 — 블록 리스트 대본
- [x] 스모크 승인 루프(`lang_ai_agent.smoke.run_scenarios`)를 대본 모델 + MockEffects로 검증 — 실모델 호출 0건, `SEND_MODE`는 강제 dry_run

**감사 001 대응 (T16 — `docs/001_ADVERSARIAL_CODE_AUDIT.md` §5)**
- [x] 같은 승인의 동시 `/approve` 2건 → effect 1회 실행, 진 쪽은 409 또는 `error` 1개 (AUD-001)
- [x] 같은 스레드 동시 `/messages` 2건 → 직렬 실행(메시지 순서 H·A·H·A), 승인 대기 중 `/messages` → 409 (AUD-002)
- [x] 사용자 턴 2회 뒤 usage 누적 = 두 턴의 합(그래프·API 양쪽) (AUD-003)
- [x] `DELETE /threads/{id}` → 204, 이후 `/state` 404; 기록 없는 id → 404 (AUD-004 최소)
- [x] Content-Length 64 KiB 초과 → 413(본문 파싱 전), `content` 8,000자 초과 → 422; 긴 effect 인자는 preview만 잘리고 초안·실행은 원본 (AUD-005)
- [x] 스트림 종료 후 `aget_state` 실패 → 정확히 한 번의 `error`, `usage`/`done` 없음, 로그에 exc_info (AUD-006)
- [x] 도구 예외·스트림 예외·MCP 실패는 `타입: 첫 줄(≤200자)`만 노출, 경로·후속 줄 미노출 (AUD-007)
- [x] `.env` 원자적 기록: rename 실패 시 원본 보존·임시파일 없음, symlink 거절 (AUD-009; AUD-008 `compare_digest`는 코드 검토로 확인)
- [x] MCP 서버 무응답 → 타임아웃 `McpConfigError`(서버명·시간), 서버 실패 → 서버명 + 첫 줄 (§3 MCP)

## 5. 수동 스모크 (사람 전용 — scripts/smoke.py)

`make smoke`(= `lang-ai-agent smoke`): 실모델로 시나리오 1(조회) + 시나리오 2(발송 초안 → 인터럽트 → 콘솔 y/n) 재현. `SEND_MODE`는 `.env` 값과 무관하게 dry_run으로 **강제** — 실발송(live)은 스모크에 포함하지 않는다. `--mcp` 플래그 시 `mcp_servers.json`(또는 `MCP_SERVERS_PATH`)의 실 retail-mcp를 stdio로 물려 재현. 1회 비용은 모델 호출 3~4회(Sonnet 급에서 수 센트). 콘솔 루프 자체는 §4 "온보딩·설정"대로 대본 모델로 테스트하고, 실모델 실행은 사람이 결정한다(WORKFLOW §4).

## 6. 커버리지

- `src/lang_ai_agent/core/` 90% 이상 (pytest-cov, T10에서 리포트). adapters는 스모크 보완.
- 2026-09-05(T10): `make test`가 `coverage report --fail-under=90 --include='src/lang_ai_agent/core/*'`로 이 기준을 강제한다. 현재 core 100%(graph 130/18, state 16/0, tools_spec 14/2 — 누락 0), 프로젝트 전체 100%. §4 체크리스트는 전 항목 커버(그래프·승인 게이트 T5, 영속·재시작 T10 e2e, 도구·에러 T5/T9, API·SSE T7, usage T8).
