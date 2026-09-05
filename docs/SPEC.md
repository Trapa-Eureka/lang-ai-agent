# SPEC — lang_ai_agent v0.1

작성: 2026-09-04 · 상태: 확정 (변경 시 이 문서를 먼저 수정)
개정: 2026-09-04 — PyPI 배포를 v0.1 목표로 추가, npm(JS/TS 클라이언트 SDK) 배포는 착수 보류(T12)
개정: 2026-09-05 — 셀프서비스 온보딩(목표 8) 추가, GitHub Actions CI를 v0.2에서 v0.1로 당김(T11)

## 1. 배경

LangChain/LangGraph 에이전트 백엔드는 현재 글로벌 원격 계약 시장에서 수요·단가가 가장 높은 항목이다. 반면 계약 입찰에서 갈리는 건 "에이전트를 돌려봤다"가 아니라 **프로덕션 패턴을 갖췄는가**다: 스트리밍, 영속 상태와 재시작 내성, 사람 승인(HITL), 결정론 테스트, 관측성. 이 레포는 그 패턴 전부를 갖춘 레퍼런스를 만들어,

- 공개 포트폴리오(계약 입찰·기술 검증용)로 쓰고,
- MCP 자동화 코어의 **에이전트 런타임 계층**으로 재사용한다 — 도구가 곧 자체 MCP 서버들(retail-mcp 등)이기 때문.

데모 도메인 **Ops Copilot**: 다지점 리테일 운영 질의·재주문 메일 발송. 도메인은 데모일 뿐, 구조는 도메인 불문 재사용이 목표다.

## 2. v0.1 목표

1. **커스텀 StateGraph 에이전트**: LLM 도구 호출 루프 + **부작용 도구는 `interrupt()` 승인 게이트** 필수 경유. 승인/거절/수정 후 `Command`로 재개.
2. **영속·재시작 내성**: 체크포인터 기반. 서버 재시작 후에도 같은 thread_id로 인터럽트 지점부터 재개된다.
3. **HTTP API (FastAPI)**: 스레드 생성 → 메시지 전송(SSE 스트림: 토큰·도구 이벤트·인터럽트) → 상태 조회 → 승인/거절 재개. Bearer 토큰 인증.
4. **도구 계층**: 조회형(safe) / 부작용형(effect, requires_approval) 분류 규약. v0.1 도구는 retail-mcp 스키마를 미러한 페이크 3종(`check_stockout`, `get_reorder_suggestions`, `send_reorder_email`) + MCP 로더(`mcp_servers.json` → `MultiServerMCPClient`)로 실 MCP 서버 연결 경로.
5. **결정론 테스트**: 실 LLM 없이 ScriptedChatModel로 그래프 궤적 전체를 검증 (`docs/TESTING.md`).
6. **관측성 최소셋**: 토큰·비용 집계(스레드별), 구조화 로그, LangSmith 트레이싱 옵션(env로 on/off).
7. **PyPI 배포**: `pyproject.toml` 배포 메타데이터 정비 → TestPyPI 검증 → 정식 PyPI에 설치 가능한 패키지로 배포 (DESIGN §10).
8. **셀프서비스 온보딩**: 설치한 사람이 `lang-ai-agent init`으로 자기 프로바이더(Anthropic 기본 / OpenAI / xAI / Google) API 키를 `.env`에 넣고 `lang-ai-agent serve`로 기동한다. 키 누락은 첫 모델 호출이 아니라 기동 시점에 수정 방법과 함께 실패한다 (DESIGN §8.1).

## 3. v0.1 비목표

- 멀티 에이전트(supervisor/서브그래프) — v0.2
- PostgresSaver·수평 확장 배포 — v0.2 (v0.1은 SqliteSaver 단일 서버)
- 평가 하니스(eval셋·LLM-as-judge·궤적 회귀) — v0.3
- RAG/벡터 검색, 장기 메모리 — 별도 판단
- 멀티테넌시·과금 — 계약 납품 시 클라이언트별 포크 전략으로 대응 (미결 §8)
- 웹 프론트엔드 — API + `scripts/` 데모 클라이언트까지만
- **npm(JS/TS 클라이언트 SDK) 배포** — 별도 신규 패키지가 필요한 작업이라 착수 보류. PyPI 배포(목표 7) 안정화 후 재논의 (`docs/TASKS.md` "보류" 섹션)

## 4. 대표 시나리오

1. **조회(승인 불필요)** — "본점에서 다음 주에 떨어질 품목?" → agent가 `check_stockout` 호출 → 표 요약 스트리밍. 인터럽트 없음.
2. **부작용(승인 필수)** — "위험 품목 재주문 메일 보내줘" → 제안 조회 → 메일 초안 → **인터럽트**(초안·수신자 표시) → 클라이언트가 `/approve` → 발송 → 결과 보고.
3. **거절·수정** — 인터럽트에서 `approved=false` + 코멘트 → 발송 없이 초안 수정 재제시 또는 정중한 종료.
4. **재시작 내성** — 2번 인터럽트 상태에서 서버 재시작 → 같은 thread_id로 `/approve` → 정상 재개·발송.

## 5. 성공 기준 (v0.1 완료 판정)

- 시나리오 1~4가 **e2e-mock**(ScriptedChatModel + 페이크 도구)으로 API 레벨에서 전부 통과.
- 부작용 도구가 승인 게이트를 우회하는 경로가 없음을 그래프 구조 테스트로 증명.
- `make check` 통과, `src/lang_ai_agent/core/` 커버리지 90% 이상.
- 수동 스모크: 실 Claude 모델 1회 + (옵션) 실 retail-mcp stdio 연결로 시나리오 1 재현. — 2026-09-05 완료: `anthropic:claude-sonnet-4-5`로 시나리오 1 정상(도구 호출·token 스트리밍·usage, 1회 ≈ 2.0k 입력/0.3k 출력 토큰 ≈ $0.01). 이 과정에서 실모델에서만 드러나는 결함 2건(`.env` 미내보냄, 블록 리스트 content) 발견·수정.
- 온보딩: 설치 후 `lang-ai-agent init` → `lang-ai-agent serve`만으로 자기 키로 응답을 받을 수 있다 (목표 8, T11).
- 포트폴리오 준비물: 영어 README 초안 + 데모 스크립트 (T11).
- TestPyPI 배포 성공 + 정식 PyPI 배포 1회 이상 완료 (T13~T14).

## 6. 로드맵

| 버전 | 내용 | 전제 |
|---|---|---|
| v0.1 | 단일 에이전트 그래프 + 승인 게이트 + FastAPI SSE + 결정론 테스트 + 온보딩 CLI + GitHub Actions CI(`make check`) + PyPI 배포 + 공개 준비 | — |
| v0.2 | PostgresSaver, supervisor 멀티에이전트, 실 retail-mcp 상시 연결 | v0.1 공개 |
| v0.3 | 평가 하니스(골든 궤적 회귀 + eval셋), 비용 리포트, Docker 배포 템플릿 | — |
| v0.4 | MCP 코어 편입 판단 — 코어의 버티컬 에이전트들을 이 런타임 위로 이관 | 코어 MVP 검증 |

## 7. 포트폴리오 산출 기준 (공개 시점)

- README는 영어로 전환(내부 docs는 한국어 유지), 아키텍처 다이어그램 1장, 60초 데모(터미널 녹화) 1개.
- "왜 이렇게 설계했나" 섹션: 승인 게이트·결정론 테스트·상태 비대화 방지 — 계약 인터뷰에서 그대로 말할 수 있는 근거.
- README에 PyPI 배지 + `pip install`/`uv add` 설치 안내 포함 (T15).

## 8. 미결 사항

- [ ] 기본 모델 티어(비용 vs 데모 품질) — 2026-09-05 스모크: `claude-sonnet-4-5`로 시나리오 1 정상(1회 ≈ $0.01, 응답 8초). 다른 티어 비교는 아직. 최종 결정은 사람(WORKFLOW §4)
- [ ] retail-mcp 상시 연결 시 프로세스 관리(stdio 수명) 방식
- [ ] 계약 납품 표준: 클라별 포크 vs 코어 라이브러리화 — v0.2 전 결정
