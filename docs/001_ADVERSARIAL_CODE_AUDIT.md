# 001 — 전수 적대적 코드 검사 보고서

- 검사일: 2026-09-05 (Asia/Manila)
- 대상: 추적 중인 Python 코드 53개 전체(`src/` 18개, `tests/` 34개, `scripts/` 1개),
  패키징·품질·실행 설정 전체
- 방식: 정적 검사, strict 타입 검사, 전체 테스트·분기 커버리지, 바이트코드 컴파일,
  오프라인 sdist/wheel 빌드, 운영 실패 경로 수동 추적
- 결론: 문법·타입·린트·기존 테스트 실패는 없으나, 운영 전 반드시 보완할 고위험 동시성
  결함 2건과 상태 정확성 결함 1건을 포함해 총 9건을 발견했다.

## 1. 자동 검사 결과

| 검사 | 결과 |
|---|---|
| `ruff check .` | 통과, 오류 0 |
| `ruff format --check .` | 통과, 55개 파일 형식 정상 |
| `pyright` (strict) | 통과, 오류·경고 0 |
| `pytest` | 통과, 176개 |
| 전체 라인/분기 커버리지 | 100% / 100% |
| `core/` 품질 게이트 | 100%, 요구치 90% 이상 |
| `python -m compileall -q src tests scripts` | 통과 |
| `uv build --offline` | sdist 및 wheel 생성 성공 |

실행 시 샌드박스 밖의 기본 uv 캐시에 접근할 수 없어 `UV_CACHE_DIR=/tmp/hawkfish-uv-cache`를
사용했다. 코드 결함은 아니며 같은 환경에서 전체 게이트는 정상 통과했다.

의존성 CVE 검사는 로컬에 `pip-audit`, `bandit`, `semgrep`이 설치되어 있지 않고 이번 검사는
네트워크 없는 조건으로 수행되어 포함하지 않았다. 따라서 “취약 의존성 없음”을 보증하지 않는다.

### 검사 파일 범위 확인

- 제품 코드: `src/lang_ai_agent/**/*.py` 18개 전부
- 테스트·테스트 헬퍼: `tests/**/*.py` 34개 전부
- 실행 스크립트: `scripts/smoke.py` 1개
- 코드 동작에 영향을 주는 설정: `pyproject.toml`, `uv.lock`, `Makefile`, `.python-version`,
  `.github/workflows/ci.yml`, `.env.example`, `mcp_servers.json.example`, `.gitignore`
- 제외: `.venv`, `dist`, 캐시, `__pycache__`, `.coverage`, `.DS_Store`, Git 내부 객체처럼
  생성되거나 외부에서 관리되는 파일

Markdown 문서는 구현 계약과 코드의 불일치를 확인하는 참고자료로 사용했지만, 검사의 대상은
문서에 한정하지 않았다. 모든 제품 Python 파일은 lint·strict typecheck·compile 검사 대상이었고,
테스트 실행 시 제품 코드 18개 전부가 라인 및 분기 커버리지 100%로 실행되었다.

## 2. 발견 사항

### AUD-001 [높음] 동일 승인 요청의 동시 처리로 부작용이 중복 실행될 수 있음

- 위치: `src/lang_ai_agent/api/app.py:211-227`, `src/lang_ai_agent/core/graph.py:267-305`
- 상태: 코드 경로 확인, 동시성 부하 테스트 부재
- 근거: `/approve`는 상태 조회와 `Command(resume=...)` 실행 사이를 원자화하지 않는다. 같은
  `thread_id`에 두 요청이 동시에 오면 둘 다 `state.interrupts`를 확인한 뒤 같은 승인 체크포인트를
  재개할 수 있다. 외부 이메일/API 호출은 체크포인트 기록보다 먼저 일어나므로 optimistic conflict가
  있더라도 이미 발생한 부작용을 되돌릴 수 없다.
- 영향: 중복 이메일, 중복 주문·결제 등. 실제 effect 도구를 붙이면 치명적이다.
- 권고: `thread_id`별 단일 실행 락을 두고, 운영 확장 시 DB 기반 lease/transaction과 effect
  idempotency key(`tool_call_id`)를 함께 적용한다. 승인 소비 여부도 원자적으로 기록한다.

### AUD-002 [높음] 같은 스레드의 메시지·승인 실행이 직렬화되지 않음

- 위치: `src/lang_ai_agent/api/app.py:183-193`, `src/lang_ai_agent/api/app.py:211-227`
- 상태: 코드 경로 확인, 경쟁 조건 테스트 부재
- 근거: 메시지 전송과 승인은 동일 체크포인터를 공유하지만 스레드별 락이 없다. 승인 대기 중에도
  `/messages`가 허용되며, 요청은 `pending=None`인 새 입력을 제출한다. 두 메시지 또는 메시지와
  승인이 겹치면 같은 스레드에서 분기된 체크포인트와 모델 호출이 만들어질 수 있다.
- 영향: 승인 유실, 메시지 순서 역전, 중복 모델·도구 호출, 사용량 오염.
- 권고: 스레드별 실행을 직렬화하고, interrupt 중 `/messages`는 409로 거절하거나 “기존 승인을
  명시적으로 취소한 뒤 새 턴 시작”이라는 API를 별도로 정의한다.

### AUD-003 [중간] 스레드 누적 사용량이 새 메시지마다 초기화됨

- 위치: `src/lang_ai_agent/api/app.py:187-191`, `src/lang_ai_agent/core/graph.py:241-243`
- 상태: 확정
- 근거: 매 `/messages` 요청이 `usage=Usage()`를 입력한다. `usage`에는 reducer가 없으므로 기존
  체크포인트의 누적값을 0으로 덮고 그 요청의 모델 호출만 다시 더한다. 문서와 모델은 “스레드별
  누적”이라고 명시하지만 다중 턴 테스트가 이를 검증하지 않는다.
- 영향: 비용·토큰 관측값 과소 집계, 예산/과금 판단 오류.
- 권고: 기존 상태를 조회해 누적값을 보존하거나 usage 전용 reducer를 정의한다. 최소 2개 사용자
  턴의 누적값을 검증하는 API 테스트를 추가한다.

### AUD-004 [중간] 메시지와 체크포인트가 무제한 증가함

- 위치: `src/lang_ai_agent/core/state.py:40-50`, `src/lang_ai_agent/adapters/checkpoint.py:53-70`
- 상태: 확정 설계 위험
- 근거: `add_messages`는 스레드 전체 메시지를 계속 누적하고 SQLite 체크포인터에는 보존기간,
  최대 턴 수, 압축/요약, 삭제 API가 없다. 각 실행의 과거 체크포인트까지 저장된다.
- 영향: 장기 실행 시 모델 컨텍스트·프로세스 메모리·SQLite 파일·지연시간이 지속 증가한다.
  전통적인 미해제 객체 누수라기보다 무제한 보존에 의한 논리적 자원 누수다.
- 권고: 메시지 window/요약 정책, thread TTL 및 checkpoint pruning, 삭제 API, DB 크기 지표와
  한도를 추가한다.

### AUD-005 [중간] 요청·도구 인자 크기 제한이 없어 메모리/디스크 DoS가 가능함

- 위치: `src/lang_ai_agent/api/app.py:114-120`, `src/lang_ai_agent/core/graph.py:272-277`
- 상태: 확정
- 근거: 메시지, 승인 코멘트, 모델이 만든 effect 인자에 최대 길이가 없다. `PendingAction`은
  `args_preview=dict(call["args"])`로 이메일 본문 등 전체 인자를 복제하여 체크포인트와 응답에 넣는다.
- 영향: 단일 인증 토큰이 노출되거나 오작동 클라이언트가 생기면 큰 요청/응답으로 메모리와 DB를
  빠르게 소진할 수 있다.
- 권고: ASGI/body 제한과 Pydantic `max_length`, 도구 인자별 한도, preview 절단·해시/외부 저장을
  적용한다.

### AUD-006 [중간] SSE 스트림 종료 후 예외가 `error` 이벤트로 변환되지 않음

- 위치: `src/lang_ai_agent/api/sse.py:147-178`
- 상태: 확정
- 근거: `try/except`는 `graph.astream_events(...)` 반복만 감싼다. 이후 `graph.aget_state`,
  interrupt payload 역참조, usage 역참조에서 발생한 DB·직렬화·스키마 오류는 그대로 빠져나간다.
  함수 문서의 “Any exception ... becomes a single error event” 보장과 다르다.
- 영향: 클라이언트는 정상적인 `error`/`done` 종결 이벤트 없이 연결 종료만 보게 된다.
- 권고: 후처리까지 예외 경계에 포함하고, 이미 일부 이벤트를 전송한 뒤에도 정확히 한 번의
  `error` 종결을 보장하는 테스트를 추가한다.

### AUD-007 [중간] 내부 예외 문자열이 API로 그대로 노출됨

- 위치: `src/lang_ai_agent/core/graph.py:175-180`, `src/lang_ai_agent/api/sse.py:168-170`
- 상태: 확정
- 근거: 도구 및 그래프 예외의 `str(exc)`를 ToolMessage/SSE에 직접 전달한다. 외부 SDK나 MCP
  오류에는 URL, 파일 경로, 요청 일부, 환경 세부정보가 들어갈 수 있다.
- 영향: 인증된 클라이언트에 내부 구조 또는 민감 데이터가 노출되고, 그 문자열이 다시 모델
  컨텍스트에 들어간다.
- 권고: 외부 응답은 안정된 오류 코드와 정제된 메시지를 사용하고 상세 traceback은 서버 로그에만
  correlation ID와 함께 기록한다.

### AUD-008 [낮음] Bearer 토큰 비교가 상수 시간 비교가 아님

- 위치: `src/lang_ai_agent/api/auth.py:30-40`
- 상태: 확정, 현실적 악용 가능성은 배포 환경에 따라 낮음
- 근거: 일반 문자열 `!=`를 사용한다.
- 영향: 매우 정밀한 반복 측정이 가능한 환경에서는 timing side channel 가능성이 있다.
- 권고: `secrets.compare_digest(credentials.credentials, expected_token)`을 사용한다.

### AUD-009 [낮음] `.env` 갱신이 원자적이지 않고 심볼릭 링크를 추적함

- 위치: `src/lang_ai_agent/cli.py:83-102`
- 상태: 확정, 로컬 CLI 공격면
- 근거: `touch`/`chmod` 후 `write_text`로 기존 파일을 직접 truncate한다. 중간 실패 시 빈/부분
  파일이 남고, `--force`에서 대상이 symlink면 링크 목적지를 덮어쓴다.
- 영향: 설정·키 손실 또는 로컬 환경에서 의도하지 않은 파일 변경.
- 권고: 같은 디렉터리에 mode 0600 임시 파일을 생성해 `fsync` 후 `os.replace`하고, symlink는
  명시적으로 거절한다.

## 3. 통과했지만 추가 확인이 필요한 영역

- 실제 LLM·실 MCP·외부 네트워크는 저장소 가드레일에 따라 실행하지 않았다.
- MCP 도구 로딩은 서버별 순차 실행이며 명시적 startup timeout이 없다
  (`src/lang_ai_agent/adapters/mcp_loader.py:151-165`). 서버가 멈추면 애플리케이션 기동도 무기한
  대기할 수 있으므로 timeout/부분 실패 정책이 필요하다.
- `POST /threads`는 ID만 발급하고 저장하지 않아 임의 문자열로 `/messages`를 호출해도 새 스레드가
  만들어진다. 현재 단일 공유 토큰 설계에서는 직접 권한 상승은 아니지만, API 계약과 404 안내가
  실제 동작과 불일치한다.

## 4. 수정 우선순위

1. AUD-001/002: per-thread 직렬화 + effect idempotency를 함께 구현한다.
2. AUD-003: 다중 턴 usage 누적을 바로잡고 회귀 테스트를 추가한다.
3. AUD-004/005: 입력·상태·체크포인트 수명과 크기 한도를 정한다.
4. AUD-006/007: SSE 오류 경계와 외부 오류 정제를 수정한다.
5. AUD-008/009 및 MCP timeout을 방어적으로 보강한다.

현재 결과는 “자동 품질 게이트 통과”이지 “운영 결함 없음” 판정이 아니다. 특히 실제 부작용 도구를
연결하기 전에 AUD-001과 AUD-002를 해소해야 한다.

## 5. 조치 결과 (2026-09-05, T16 — `docs/TASKS.md`)

각 항목을 코드로 재검증한 뒤 조치했다. 판정은 9건 전부 타당. 회귀 테스트는 `docs/TESTING.md` §4
"감사 001 대응", 설계 반영은 `docs/DESIGN.md` §2/§3/§5/§6/§7/§11.

| ID | 조치 | 위치 |
|---|---|---|
| AUD-001 | 수정: thread_id별 `asyncio.Lock`(`ThreadLocks`)으로 `/messages`·`/approve` 스트림과 `DELETE`를 직렬화. 락 안에서 승인 대상(`tool_call_id`)을 재확인해 중복 요청은 `error` 이벤트 1개(또는 락 밖 검사에서 409). 다중 프로세스용 DB lease·effect 멱등키는 v0.2(DESIGN §11) | `api/thread_locks.py`, `api/app.py` |
| AUD-002 | 수정: 같은 락. 승인 대기 중 `/messages`는 409(거절 코멘트가 곧 취소, 이후 새 턴) | `api/app.py` |
| AUD-003 | 수정: `usage`에 합산 리듀서 `add_usage`, agent 노드는 호출 1건 증분(`usage_of_call`)만 반환. 그래프·API 2턴 누적 테스트 | `core/state.py`, `core/graph.py` |
| AUD-004 | 부분: `DELETE /threads/{id}`(체크포인터 `adelete_thread`) 추가. 메시지 윈도/요약, 체크포인트 정리·TTL, 크기 지표는 v0.2로 이월(SPEC §6, DESIGN §11) | `api/app.py` |
| AUD-005 | 수정: `content` ≤ 8,000자·`comment` ≤ 2,000자(422), Content-Length 64 KiB 초과는 순수 ASGI 미들웨어가 본문을 읽기 전에 413. `args_preview`는 값별 120자 요약으로 바꾸고 실행은 원본 tool_call을 id로 재조회 — 이 과정에서 preview를 실행 인자로 쓰던 파생 결함도 수정. 초안(`draft`)은 사람이 전체를 봐야 하므로 유지 | `api/limits.py`, `core/graph.py`, `core/state.py` |
| AUD-006 | 수정: 스트림 종료 후 처리(`aget_state`·인터럽트·usage 조회)까지 예외 경계 안으로. 일부 이벤트 전송 뒤에도 `error` 정확히 1회 테스트 | `api/sse.py` |
| AUD-007 | 수정: 노출 문자열은 `describe_error()`(예외 타입 + 첫 줄 200자)뿐. 전체 traceback은 로그(`exc_info`, thread_id·tool·error_type). 안정 오류 코드는 필요해질 때 SSE 스키마에 추가 | `core/errors.py`, `core/graph.py`, `api/sse.py`, `adapters/mcp_loader.py` |
| AUD-008 | 수정: `secrets.compare_digest`(바이트 비교 — 비ASCII 입력에도 예외 없음) | `api/auth.py` |
| AUD-009 | 수정: 같은 디렉터리 임시파일(0600, `mkstemp`) + `fsync` + `os.replace`, 실패 시 임시파일 제거·원본 보존, symlink는 `--force`여도 거절 | `cli.py` |
| §3 MCP | 수정: 서버별 기동 타임아웃 30초(`startup_timeout_s`), 초과·실패 시 서버명·명령을 담은 `McpConfigError` | `adapters/mcp_loader.py` |
| §3 threads | 문서화: 스레드 id는 클라이언트가 고르는 이름공간이라는 의도된 설계를 DESIGN §5에 명시 | `docs/DESIGN.md` |

검증: `make check` 통과 — ruff·pyright strict 오류 0, 208 passed(감사 전 176), 라인·분기 커버리지 100%. 동시성 테스트(AUD-001/002)는 5회 반복 실행으로 안정성 확인.
