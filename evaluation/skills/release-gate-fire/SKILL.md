---
name: release-gate-fire
description: Fire release-gate cases to a live matmaster-evo environment (test/uat/prod) via the stream API. Sessions appear in the frontend grouped by directory.
---

# Release Gate Fire

Batch-fire `evaluation/release_gate/cases.yaml` to a live matmaster-evo environment.
Each case becomes an independent session visible in the frontend.

## Prerequisites

Read from `.env.{env}` (fallback `.env.test`):
- `BOHRIUM_USER_ID` — Bohrium numeric user ID
- `BOHRIUM_ORG_ID` — Bohrium organization ID (required for access_key retrieval)
- `BOHRIUM_PROJECT_ID` — Bohrium project ID (must match frontend's project to appear in session list)

## Environment URLs (Direct Connection)

Scripts connect directly to matmaster-evo service (bypasses gateway, no JWT needed):

| Env  | Direct URL                                              |
|------|---------------------------------------------------------|
| test | `https://matmaster-evo.test.bohrium.com/api/v1/chat`    |
| uat  | `https://matmaster-evo.uat.bohrium.com/api/v1/chat`     |
| prod | `https://matmaster-evo.bohrium.com/api/v1/chat`         |

Gateway URLs (require JWT, for reference only):

| Env  | Gateway URL                                                              |
|------|--------------------------------------------------------------------------|
| test | `https://matmaster.test.bohrium.com/bohrapi/v1/matmaster-evo/api/v1/chat` |
| uat  | `https://matmaster.uat.bohrium.com/bohrapi/v1/matmaster-evo/api/v1/chat`  |
| prod | `https://matmaster.bohrium.com/bohrapi/v1/matmaster-evo/api/v1/chat`      |

## API Contract

```
POST {base}/sessions/{session_id}/stream
Headers:
  X-User-Id: <bohrium_user_id>
  X-Org-Id: <bohrium_org_id>        ← required for Bohrium access_key
  Content-Type: application/json
Body:
  {
    "content": "<case prompt>",
    "mode": "direct",
    "model": "global.anthropic.claude-opus-4-6-v1",
    "directory": "<grouping directory>",
    "bohrium_project_id": "<from .env.{env}>"
  }
Response: SSE stream (event: ag-ui, data: JSON)

PUT {base}/sessions/{session_id}/session-directory
Headers: (same)
Body: { "directory": "<grouping directory>" }
```

### Key Learnings

1. **Session auto-creates** on first POST stream (no separate create call needed).
2. **Worker runs independently** of SSE connection — close after confirming HTTP 200.
3. **`X-Org-Id` header is required** — without it, Bohrium access_key lookup fails.
4. **`bohrium_project_id` in body is required** — without it, worker aborts with "project_id 缺失".
5. **`model` in body controls which LLM** — omitting it uses server default (qwen_3_6_plus).
6. **`directory` in body only writes to event history** — it does NOT persist to session metadata. Must call `PUT /session-directory` after fire for the session to appear grouped in the frontend list.
7. **Frontend list filters by `project_id`** — the `bohrium_project_id` you pass must match the project the user has selected in the frontend, otherwise the session won't appear in the sidebar.

## Workflow

### Step 1: Determine Parameters

Ask the user if not provided:
- **env**: `test` (default) / `uat` / `prod`
- **model**: LLM route key (default: `global.anthropic.claude-opus-4-6-v1`)
- **directory**: Grouping path (default: `/share/eval/release_gate_<YYYYMMDD_HHMM>`)
- **cases**: All (default) or specific IDs like `rg_01,rg_05`

Other params auto-resolve from `.env.{env}`: user_id, org_id, project_id.

### Step 2: Fire Cases

```bash
uv run python evaluation/skills/release-gate-fire/scripts/fire_release_gate.py \
  --env test \
  --cases all
```

Fire specific cases with a custom model:

```bash
uv run python evaluation/skills/release-gate-fire/scripts/fire_release_gate.py \
  --env test \
  --cases rg_01,rg_05,rg_09 \
  --model claude-sonnet-4-6
```

All available options:

```
--env test|uat|prod          Target environment (default: test)
--user-id ID                 Bohrium user ID (default: from .env.{env})
--org-id ID                  Bohrium org ID (default: from .env.{env})
--bohrium-project-id ID      Bohrium project ID (default: from .env.{env})
--model KEY                  Model route key (default: global.anthropic.claude-opus-4-6-v1)
--mode direct|planner        Agent mode (default: direct)
--directory PATH             Session grouping directory
--cases all|rg_01,rg_05      Which cases to fire
--delay SECONDS              Delay between fires (default: 2.0)
--dry-run                    Print plan without firing
```

### Step 3: Verify

After firing, the script prints session IDs. Check frontend at:
- test: `https://matmaster.test.bohrium.com/matmaster/chat-evo/<session_id>`
- uat: `https://matmaster.uat.bohrium.com/matmaster/chat-evo/<session_id>`
- prod: `https://matmaster.bohrium.com/matmaster/chat-evo/<session_id>`

Sessions appear in sidebar grouped under the `directory` you specified.

Verify via API:

```bash
curl -s -H "X-User-Id: <user_id>" \
  "https://matmaster-evo.<env>.bohrium.com/api/v1/chat/sessions/list?project_id=<project_id>&per_group_limit=20" \
  | python -m json.tool
```

## Hard Rules

- **Never fire to prod without explicit user confirmation.**
- Fire-and-forget: read only until first SSE event confirmed, then close connection.
- Default concurrency: sequential (1 at a time, 2s delay) to avoid queue flooding.
- Session ID format: `rg-{case_id}-{env}-{timestamp}-{uuid6}` for traceability.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| 401 | Gateway requires JWT | Use direct URL (matmaster-evo.{env}.bohrium.com), not gateway |
| 403 quota | User quota exhausted | Use a service account or ask admin to reset |
| 409 conflict | Session already running | Wait or use a fresh session_id (script auto-generates unique IDs) |
| 503 queue | REDIS_URL not configured on target | Backend deployment issue, ping ops |
| "Bohrium project_id 缺失" | Missing `bohrium_project_id` in body | Ensure `.env.{env}` has `BOHRIUM_PROJECT_ID` |
| "Bohrium access_key 获取失败" | Missing `X-Org-Id` header | Ensure `.env.{env}` has `BOHRIUM_ORG_ID` |
| Session not in frontend list | `project_id` mismatch or directory not persisted | Verify `BOHRIUM_PROJECT_ID` matches frontend project; script now auto-PUTs session-directory |
| Model shows "default" | `model` field not passed in body | Use `--model` flag (default is now opus) |
