# lang_ai_agent

**LangGraph 기반 프로덕션 AI Agent 백엔드** — 글로벌 계약 시장 납품 표준을 겨냥한 레퍼런스 구현이자, MCP 자동화 코어의 에이전트 런타임 계층.

한 프로젝트로 두 개의 포트폴리오를 만든다:

1. **글로벌 계약용 쇼케이스** — 클라이언트가 실제로 요구하는 프로덕션 패턴(스트리밍 API, 영속 상태·재시작 내성, 사람 승인 게이트, 결정론 테스트, 관측성)을 전부 갖춘 공개 레포.
2. **자체 생태계의 런타임** — 이 에이전트의 도구는 MCP 서버다. `langchain-mcp-adapters`로 retail-mcp 같은 자체 MCP 서버를 도구로 물려, "내 MCP 서버들을 오케스트레이션하는 에이전트 백엔드"라는 스토리를 완성한다.

데모 도메인은 **Ops Copilot**: "품절 위험 확인하고 재주문 메일 보내줘" → 조회 도구 호출 → 메일 초안 → **사람 승인 인터럽트** → 승인 시 발송. 조회는 자유, 부작용은 반드시 승인 — sheet_mcp/retail-mcp의 이중 게이트 철학을 LangGraph `interrupt()`로 구현한 것이다.

## 문서 맵

| 문서 | 내용 | 읽는 시점 |
|---|---|---|
| `CLAUDE.md` | 에이전트 스티어링 — 스택, 명령어, 규칙, 가드레일 | 모든 에이전트 세션 시작 시 (자동 로드) |
| `docs/SPEC.md` | 제품 스펙 — 목표/비목표, 시나리오, 로드맵 | 기능 논의·범위 판단 전 |
| `docs/DESIGN.md` | 기술 설계 — 그래프, 상태, API, 도구 분류, MCP 연결 | 구현 전 필독 |
| `docs/TESTING.md` | 테스트 전략 — ScriptedChatModel 결정론, 골든 궤적 | 테스트 작성 전 |
| `docs/TASKS.md` | 태스크 백로그 — 에이전트 실행 단위, 완료 기준 | 작업 배정 시 |
| `docs/WORKFLOW.md` | AI-native 개발 규칙 (공통 + 이 레포 특이사항) | 최초 1회 + 운영 중 참조 |

## 개발 방식

sheet_mcp/retail-mcp와 동일: **문서 → 에이전트 구현 → 검증**. 사람(Jin)은 스펙·리뷰·실모델 스모크·공개 승인을 맡고, 구현은 Claude Code가 `docs/TASKS.md` 단위로. 공통 게이트는 `make check`.

## 퀵스타트 (T0 완료 후 유효)

```bash
uv sync
make check        # ruff + pyright + pytest — 공통 게이트
make dev          # FastAPI 서버 (uvicorn, SSE 스트리밍)
make smoke        # 실 모델 1회 수동 스모크 (사람 전용)
```

## 상태

- 2026-09-04: 문서 단계 (코드 미작성). T0부터 시작.
- 공개 마일스톤: v0.1 완료 시 GitHub(Trapa-Eureka)에 영어 README + 데모와 함께 퍼블리시 (T11).
- 2026-09-04: PyPI 배포를 v0.1 정식 목표로 추가 확정(T12, `docs/SPEC.md` §2/§7). npm(JS/TS 클라이언트 SDK) 배포는 착수 보류(`docs/TASKS.md` "보류" 섹션 참고).
