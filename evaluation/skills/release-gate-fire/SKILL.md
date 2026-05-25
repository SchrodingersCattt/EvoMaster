---
name: release-gate-fire
description: Fire release-gate cases to a live matmaster-evo environment (test/uat/prod) via the stream API. Sessions appear in the frontend grouped by directory.
---

# Release Gate Fire

Batch-fire `evaluation/release_gate/cases.yaml` to a live matmaster-evo environment.
Each case becomes an independent session visible in the frontend.

## Prerequisites

- A valid `X-User-Id` (Bohrium numeric user ID). Read from `.env.test` (`BOHRIUM_USER_ID`) or ask the user.
- Network access to the target environment gateway.

## Environment URLs

| Env  | Gateway base                                                             |
|------|--------------------------------------------------------------------------|
| test | `https://matmaster.test.bohrium.com/bohrapi/v1/matmaster-evo/api/v1/chat` |
| uat  | `https://matmaster.uat.bohrium.com/bohrapi/v1/matmaster-evo/api/v1/chat`  |
| prod | `https://matmaster.bohrium.com/bohrapi/v1/matmaster-evo/api/v1/chat`      |

## API Contract

```
POST {base}/sessions/{session_id}/stream
Headers:
  X-User-Id: <bohrium_user_id>
  Content-Type: application/json
Body:
  {
    "content": "<case prompt>",
    "mode": "direct",
    "directory": "<grouping directory>"
  }
Response: SSE stream (can be closed immediately after HTTP 200 — worker runs independently)
```

- Session auto-creates on first POST (no separate create call needed).
- Worker continues running after SSE connection closes (confirmed in code: "仅用户显式点「停止」才取消").
- Frontend shows sessions grouped by `directory` field.

## Workflow

### Step 1: Determine Parameters

Ask the user if not provided:
- **env**: `test` (default) / `uat` / `prod`
- **user_id**: Bohrium user ID (default from `.env.test` → `BOHRIUM_USER_ID`)
- **directory**: Grouping path (default: `/share/eval/release_gate_<YYYYMMDD_HHMM>`)
- **cases**: All (default) or specific IDs like `rg_01,rg_05`

### Step 2: Fire Cases

Run the following Python script. Replace placeholders with actual values.

```bash
uv run python evaluation/skills/release-gate-fire/scripts/fire_release_gate.py \
  --env test \
  --user-id 110680 \
  --directory "/share/eval/release_gate_$(date +%Y%m%d_%H%M)" \
  --cases all
```

Or fire specific cases:

```bash
uv run python evaluation/skills/release-gate-fire/scripts/fire_release_gate.py \
  --env test \
  --user-id 110680 \
  --directory "/share/eval/release_gate_$(date +%Y%m%d_%H%M)" \
  --cases rg_01,rg_05,rg_09
```

### Step 3: Verify

After firing, confirm sessions are visible:

```bash
curl -s -H "X-User-Id: <user_id>" \
  "https://matmaster.<env>.bohrium.com/bohrapi/v1/matmaster-evo/api/v1/chat/sessions/list?project_id=42&per_group_limit=20" \
  | python -m json.tool | grep -A2 "release_gate"
```

Or tell the user to check the frontend at:
- test: `https://matmaster.test.bohrium.com/matmaster/chat-evo/`
- uat: `https://matmaster.uat.bohrium.com/matmaster/chat-evo/`
- prod: `https://matmaster.bohrium.com/matmaster/chat-evo/`

## Hard Rules

- **Never fire to prod without explicit user confirmation.**
- Fire-and-forget: read only until HTTP 200 confirmed, then close connection.
- Default concurrency: sequential (1 at a time) to avoid queue flooding. Use `--parallel N` to override.
- Session ID format: `rg-{case_id}-{env}-{timestamp}` for traceability.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| 401 | Missing/invalid X-User-Id | Check BOHRIUM_USER_ID in .env.test |
| 403 quota | User quota exhausted | Use a service account or ask admin to reset |
| 409 conflict | Session already running | Wait or use a fresh session_id |
| 503 queue | REDIS_URL not configured on target | Backend deployment issue, ping ops |
