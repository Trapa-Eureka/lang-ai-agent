# DEMO — 60초 터미널 데모 시나리오 (T11)

목적: SPEC §7의 "60초 데모(터미널 녹화) 1개". 실모델 호출 3~4회(Sonnet 급 수 센트), `SEND_MODE=dry_run`이라 실발송은 없다. README의 "60-second demo"는 이 문서의 요약본.

## 준비 (녹화 전, 화면 밖)

- `uv sync && uv run lang-ai-agent init` 완료 — `.env`에 자기 키·`APP_BEARER_TOKEN`이 있는 상태.
- 터미널 2개: **좌**(서버) / **우**(클라이언트). 우측 셸에 `TOKEN=<APP_BEARER_TOKEN>`, `BASE=http://127.0.0.1:8000` export. `jq` 설치.
- 녹화 도구는 asciinema 또는 터미널 GIF. 폰트 크게, 우측 창 폭 ≥ 100칸(표가 안 잘리게).

## 타임라인

| 구간 | 창 | 무엇을 보여주나 |
|---|---|---|
| 0–5s | 좌 | `uv run lang-ai-agent serve` → JSON 로그 1줄 + "Uvicorn running". (키가 없으면 여기서 즉시 `ConfigError`로 실패한다는 점을 한 마디) |
| 5–15s | 우 | 스레드 생성 → 시나리오 1 질문 → `tool_start/tool_end`(check_stockout) 뒤 `token`이 표로 스트리밍 |
| 15–35s | 우 | 시나리오 2 "위험 품목 재주문 메일 보내줘" → 제안 조회 → **`interrupt` 이벤트**(초안·수신자)로 스트림이 멈춤 |
| 35–45s | 우 | `GET /state` → `awaiting_approval: true`, `pending.tool_name: send_reorder_email` |
| 45–55s | 우 | `POST /approve {"approved": true}` → `tool_start/tool_end`(send_reorder_email, dry_run) → 결과 보고 `token` → `usage` → `done` |
| 55–60s | 좌 | 로그에 node/tool/duration_ms가 한 줄 JSON으로 쌓인 모습 → 컷 |

## 명령어 (우측 창, 복붙용)

```bash
H=(-H "Authorization: Bearer $TOKEN" -H 'content-type: application/json')
TID=$(curl -s -X POST "${H[@]}" $BASE/threads | jq -r .thread_id)

# 시나리오 1 — 조회 (인터럽트 없음)
curl -sN -X POST "${H[@]}" $BASE/threads/$TID/messages \
  -d '{"content":"본점(store id: main)에서 다음 주에 떨어질 품목이 뭐야? 표로 요약해줘."}'

# 시나리오 2 — 부작용 (승인 필수): interrupt 이벤트에서 스트림이 멈춘다
curl -sN -X POST "${H[@]}" $BASE/threads/$TID/messages \
  -d '{"content":"위험 품목 재주문 메일을 ops@example.com으로 보내줘."}'

curl -s "${H[@]}" $BASE/threads/$TID/state | jq '{awaiting_approval, pending, usage}'

# 승인 → dry_run 발송 → 결과 보고
curl -sN -X POST "${H[@]}" $BASE/threads/$TID/approve -d '{"approved": true}'
```

거절 변형(시간이 남으면): `-d '{"approved": false, "comment": "수량을 절반으로 줄여줘"}'` → 발송 없이 수정 초안이 2차 `interrupt`로 다시 올라온다(SPEC §4-3).

## 내레이션 (3문장)

1. "조회 도구는 자유롭게 부르지만, 메일 발송 같은 부작용 도구는 그래프가 `interrupt()`에서 멈추고 사람의 승인을 기다립니다."
2. "그 지점은 체크포인트로 저장되므로 서버를 재시작해도 같은 thread_id로 이어서 승인할 수 있습니다."
3. "이 흐름 전체는 실제 LLM 없이 대본 모델로 결정론 테스트되고, 승인 게이트 우회 경로가 없다는 것은 그래프 구조 테스트가 증명합니다."

## 재시작 내성 컷 (선택, +15s)

시나리오 2의 `interrupt` 직후 좌측 창에서 서버를 Ctrl+C → 다시 `serve` → 우측에서 같은 `TID`로 `/approve`. SQLite 체크포인트가 살아 있어 발송까지 정상 재개된다(SPEC §4-4).
