# WORKFLOW — 이 레포를 굴리는 AI-native 규칙

기반: Clare Liguori (AWS), "From AI-Assisted to AI-Native: Building a Frontier Development Team"
(https://youtu.be/Ry0WHNxDbYA · AWS 블로그: https://aws.amazon.com/blogs/machine-learning/how-frontier-teams-are-reinventing-ai-native-development/)
운영 원칙은 sheet_mcp/retail-mcp의 WORKFLOW와 동일. 공통 요약 + **이 레포 특이사항**만 적는다.

## 0. 역할 정의 (프론티어 3행동)

| 행동 | 이 레포에서 |
|---|---|
| Hands-off Coding (1~2%) | Jin은 SPEC/DESIGN 수정·리뷰·스모크·공개 승인만. 구현은 에이전트 |
| Infrequent Interaction | 태스크마다 기계 판정 완료 기준 → 세션 중 개입 없이 완주 |
| Minimized Idle Time | T1 후 레인 A/B/C, T5 후 T6/T8/T9 병렬. sheet_mcp·retail-mcp와 레포 간 병렬도 가능 |

## 1. 습관 5개 → 규칙 (공통 요약)

1. **Agent Context** — 부족지식은 CLAUDE.md/docs에만. 격주 프루닝 + 로그.
2. **Slow Down to Speed Up** — 이 레포의 번역: **Python이지만 타입은 포기하지 않는다.** pyright strict + Pydantic이 tsc의 역할을 대신해 에이전트에게 가장 싼 피드백 루프를 제공한다. `# type: ignore` 남발은 이 습관의 위반.
3. **Feed, Don't Babysit** — 배정은 TASKS 템플릿 1회, 자기 검증 = `make check`.
   ```bash
   git worktree add ../lang_ai_agent-t3 -b t3 && cd ../lang_ai_agent-t3 && claude
   ```
4. **Explicit Intent** — 그래프 토폴로지·상태 스키마·SSE 계약 변경은 DESIGN diff가 코드보다 먼저.
5. **Shift Left** — 이 레포의 번역: **모델을 대본으로 대체(ScriptedChatModel)**. LLM 앱의 "로컬 결정론 목 서비스"란 곧 가짜 모델이다. 실모델은 smoke/evals에만 존재한다.

## 2. lang_ai_agent 특이사항

- **플레이키 금지 원칙**: 테스트가 흔들리면 원인은 대본·코드다. 실모델 호출을 테스트에 넣어 "그럴듯하게" 통과시키는 수정은 리뷰에서 반려한다.
- **승인 게이트는 협상 불가**: effect 도구가 approval을 우회하는 지름길 추가(편의상 플래그, 테스트 전용 백도어 포함)는 금지. 구조 불변식 테스트의 삭제·완화도 금지.
- **상태 다이어트**: 상태는 체크포인트마다 통째로 직렬화된다. 리뷰 시 상태에 큰 페이로드가 들어오는 diff를 잡아낸다.
- **공개 레포 의식**: 커밋 메시지·주석·에러 메시지는 공개돼도 부끄럽지 않게. 시크릿·클라이언트 흔적 유입 금지.

## 3. 일일 운영 루틴

1. 착수 가능 태스크 확인 → 레인별 worktree 배정 (세 레포 백로그를 하나의 큐로 취급)
2. 실행 중 개입하지 않는다 — 그 시간에 v0.2 문서·데모 시나리오를 다듬는다
3. 완료 보고 → `make check` 재실행 → diff 리뷰 → 머지 → 상태 갱신
4. 격주: CLAUDE.md 프루닝, TASKS 정리

## 4. 자율성의 한계선 (사람이 잡는 것)

- 실모델 스모크 실행(API 비용)과 기본 모델 티어 결정
- `SEND_MODE=live` 전환과 실 부작용 승인
- GitHub 공개(퍼블리시) 승인과 영어 README 최종 톤
- 시크릿·MCP 서버 경로 등 로컬 환경 구성
