# TESTING — lang_ai_agent

목적: **실 LLM 없이** 에이전트 그래프의 모든 경로를 로컬 결정론으로 검증한다. LLM 앱에서 Shift-Left의 핵심은 "모델을 대본으로 대체"하는 것 — 대본이 있으면 그래프는 그냥 상태 기계이고, 상태 기계는 완전하게 테스트된다.

## 1. 원칙

- 테스트에서 네트워크·실 LLM 호출 0건. 모델 = ScriptedChatModel, 도구 = 페이크, 발송 = 목, 체크포인터 = InMemorySaver(재시작 테스트만 임시파일 SqliteSaver).
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
- [ ] 구조 불변식: 컴파일된 그래프에서 effect_tools로 들어오는 엣지가 approval 경유뿐임을 검증 (get_graph 순회)
- [ ] 인터럽트 페이로드에 pending 요약·초안 포함, 대용량 원본 미포함
- [ ] resume 값(`approved/comment`)이 interrupt() 반환값으로 정확히 전달
- [ ] 거절 comment가 ToolMessage로 모델에 전달됨 (대본으로 후속 응답 검증)
- [ ] SEND_MODE=dry_run이면 승인돼도 MockEffects가 실발송 경로를 타지 않음 (이중 게이트)

**영속·재시작**
- [ ] 임시파일 SqliteSaver: 인터럽트 상태에서 그래프 객체 폐기 → 재컴파일(동일 DB·thread_id) → approve 재개 성공
- [ ] 스레드 격리: 두 thread_id 병행 실행, 상태 혼입 없음

**도구·에러**
- [ ] 도구 예외 → 에러 ToolMessage로 변환, 그래프는 죽지 않고 agent가 대본대로 사과·대안 제시
- [ ] 미등록 도구 호출 대본 → 명확한 실패 메시지
- [ ] MCP 로더: 설정 파싱·approval 매핑 단위 테스트 (설정 누락 도구 → effect 기본값)

**API·SSE (httpx ASGI)**
- [ ] 인증 없음/틀림 → 401
- [ ] messages 스트림 이벤트 순서: token* → tool_start/end* → (interrupt | usage → done)
- [ ] interrupt 후 GET state에 pending 노출, approve 후 done까지 스트림
- [ ] 존재하지 않는 thread_id → 수정 방법 담긴 404

**usage**
- [ ] 다중 모델 호출 누적 집계가 대본의 고정 usage 합과 일치

## 5. 수동 스모크 (사람 전용 — scripts/smoke.py)

`make smoke`: 실 Claude 모델로 시나리오 1(조회) 1회 + 인터럽트→콘솔 y/n 승인 흐름. `--mcp` 플래그 시 mcp_servers.json의 실 retail-mcp를 stdio로 물려 재현. 실발송(live)은 스모크에 포함하지 않는다.

## 6. 커버리지

- `src/lang_ai_agent/core/` 90% 이상 (pytest-cov, T10에서 리포트). adapters는 스모크 보완.
