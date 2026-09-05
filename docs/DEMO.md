# DEMO — the 60-second terminal demo (T11)

Purpose: the "one 60-second demo (terminal recording)" of SPEC §7. Three to four real-model calls (cents on a Sonnet-class model); `SEND_MODE=dry_run`, so nothing is actually sent. The README's "60-second demo" is a summary of this document.

## Setup (before recording, off screen)

- `lang-ai-agent init` done (from a checkout: `uv sync && uv run lang-ai-agent init`) — `.env` holds your key and `APP_BEARER_TOKEN`.
- Two terminals: **left** (server) / **right** (client). In the right shell export `TOKEN=<APP_BEARER_TOKEN>` and `BASE=http://127.0.0.1:8000`. `jq` installed.
- Recording tool: asciinema or a terminal GIF. Large font; right window at least 100 columns wide so the table is not cut.

## Timeline

| Segment | Window | What it shows |
|---|---|---|
| 0–5s | left | `lang-ai-agent serve` → one JSON log line + "Uvicorn running". (One sentence: without a key this fails right here with a `ConfigError`) |
| 5–15s | right | Create a thread → scenario 1 question → `tool_start/tool_end` (check_stockout), then `token` events stream the table |
| 15–35s | right | Scenario 2 "send the reorder email for the at-risk items" → suggestions fetched → the stream stops at the **`interrupt` event** (draft and recipient) |
| 35–45s | right | `GET /state` → `awaiting_approval: true`, `pending.tool_name: send_reorder_email` |
| 45–55s | right | `POST /approve {"approved": true}` → `tool_start/tool_end` (send_reorder_email, dry_run) → result report as `token` → `usage` → `done` |
| 55–60s | left | Logs piling up as one-line JSON with node/tool/duration_ms → cut |

## Commands (right window, copy-paste)

```bash
H=(-H "Authorization: Bearer $TOKEN" -H 'content-type: application/json')
TID=$(curl -s -X POST "${H[@]}" $BASE/threads | jq -r .thread_id)

# Scenario 1 — query (no interrupt)
curl -sN -X POST "${H[@]}" $BASE/threads/$TID/messages \
  -d '{"content":"Which items at the main store (store id: main) will stock out next week? Summarize as a table."}'

# Scenario 2 — side effect (approval required): the stream stops at the interrupt event
curl -sN -X POST "${H[@]}" $BASE/threads/$TID/messages \
  -d '{"content":"Send the reorder email for the at-risk items to ops@example.com."}'

curl -s "${H[@]}" $BASE/threads/$TID/state | jq '{awaiting_approval, pending, usage}'

# Approve → dry-run send → result report
curl -sN -X POST "${H[@]}" $BASE/threads/$TID/approve -d '{"approved": true}'
```

Rejection variant (if time allows): `-d '{"approved": false, "comment": "Cut the quantities in half"}'` → nothing is sent and a revised draft comes back as a second `interrupt` (SPEC §4-3).

## Narration (three sentences)

1. "Read-only tools are called freely, but for a side-effecting tool like sending an email the graph stops at `interrupt()` and waits for a human to approve."
2. "That point is saved as a checkpoint, so you can restart the server and continue the approval on the same thread_id."
3. "This whole flow is tested deterministically with a scripted model and no real LLM, and a graph-structure test proves there is no path around the approval gate."

## Restart-resilience cut (optional, +15s)

Right after scenario 2's `interrupt`, Ctrl+C the server in the left window → `serve` again → `/approve` with the same `TID` on the right. The SQLite checkpoint is still there, so it resumes and completes the send (SPEC §4-4).
