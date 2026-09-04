# CLAUDE.md — lang_ai_agent 스티어링

LangGraph 기반 프로덕션 에이전트 백엔드 (Ops Copilot 데모). 스펙은 `docs/SPEC.md`, 설계는 `docs/DESIGN.md`. **이 레포는 공개 포트폴리오가 될 것이므로 코드 품질 기준을 납품 수준으로 유지한다.**

## 스택

- Python 3.12+, 패키지 관리 **uv** (pyproject.toml 단일 소스)
- 타입: **pyright strict** + Pydantic v2 — "정적 타입이 에이전트의 가장 싼 피드백 루프"를 Python에서 재현하는 조합
- 에이전트: LangGraph (커스텀 StateGraph — prebuilt 미사용이 의도), LangChain core
- 모델: `init_chat_model` 경유 모델 불가지론, 기본 Claude(`langchain-anthropic`)
- MCP 도구: `langchain-mcp-adapters` (`MultiServerMCPClient`)
- API: FastAPI + SSE 스트리밍, 체크포인트: InMemorySaver(테스트) / SqliteSaver(개발·단일 서버)
- 검증: pytest + pytest-asyncio, ruff(lint+format), pyright

## 명령어 (Makefile)

```bash
make check        # ruff check + pyright + pytest — 태스크 완료의 필수 게이트
make test         # pytest
make lint         # ruff check + format --check
make typecheck    # pyright
make dev          # uvicorn 개발 서버
make smoke        # 실 모델·실 MCP 수동 스모크 (사람 전용)
```

## 소스 레이아웃

```
src/lang_ai_agent/
  core/        # state.py(AgentState·Pydantic 모델), graph.py(StateGraph), tools_spec.py(도구 분류 규약)
  adapters/    # llm.py(모델 팩토리), checkpoint.py, mcp_loader.py, effects.py(발송 등 부작용 구현)
  api/         # app.py(FastAPI 조립), sse.py(이벤트 매핑), auth.py
tests/         # helpers/(ScriptedChatModel, fake tools, fixed clock 포함), 단위·컴포넌트·e2e-mock
scripts/       # smoke.py — 사람 전용
mcp_servers.json.example   # MCP 도구 연결 설정 예시
```

## 컨벤션

- pyright strict에서 `# type: ignore`는 사유 주석 없이는 금지. `Any` 반환 함수 금지, 경계(요청·모델 출력·MCP 응답)는 Pydantic 파싱.
- 기본 async. 그래프 노드는 얇게 — 로직은 순수 함수로 빼서 단위 테스트 가능하게.
- **상태(State)에 대용량 페이로드 저장 금지** — 상태는 매 체크포인트마다 직렬화되므로 메시지·최소 메타만 담고, 큰 결과물은 요약해서 넣는다.
- 에러 메시지는 원인 + 수정 방법까지 (예: `mcp_servers.json이 없습니다. mcp_servers.json.example을 복사해 서버 경로를 채우세요.`).
- 커밋 메시지: `T{n}: 요약`.

## 가드레일 (위반 금지)

1. **부작용 도구(requires_approval=True)는 승인 인터럽트를 거치지 않는 실행 경로가 코드에 존재하면 안 된다.** 그래프 구조 테스트로 강제된다 — 우회 금지.
2. 테스트에서 **네트워크·실 LLM 호출 0건.** 모델은 ScriptedChatModel, MCP는 페이크 도구, 발송은 목. 실모델은 `make smoke`와 evals에만.
3. 발송류 부작용은 이중 게이트: 그래프 승인 인터럽트 **그리고** `SEND_MODE=live`. 테스트는 항상 dry_run 경로.
4. 시크릿(`ANTHROPIC_API_KEY`, `APP_BEARER_TOKEN` 등)은 `.env`만. 커밋 금지, `.env.example`만 커밋.
5. ScriptedChatModel 대본이 소진되거나 기대와 어긋나면 **조용히 넘어가지 말고 명확히 실패**시킨다 (플레이키의 씨앗 차단).
6. 스펙·설계와 코드가 충돌하면 `docs/`를 먼저 고친다. 특히 그래프 토폴로지 변경은 DESIGN §3 수정이 선행.

## 작업 방식

- 한 세션 = `docs/TASKS.md`의 한 태스크. 완료 기준 전부 충족 + `make check` 통과까지 자가 수정 루프. 스펙 모호로 막힐 때만 멈추고 질문.
- 완료 시 변경 파일·검증 결과 요약 후 종료.

## 프루닝 로그

격주 검토, 낡은 규칙 삭제 (`docs/WORKFLOW.md`).

- 2026-09-04: 최초 작성.
