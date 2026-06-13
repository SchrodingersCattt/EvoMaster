---
name: skill-manager
description: "List and upload user skills to the MatMaster skill library. Use this when the user wants to save a workflow as a reusable skill or check what skills they already have."
---

# Skill Manager

Manage the user's personal skill library via the Bohrium Open API.

> Skill-authoring rule: Do not add a `## When to Use` section. Skill loading is decided from frontmatter `description`; put trigger keywords and routing guardrails there. Body sections should describe how the loaded skill behaves.

## Prerequisites

The following environment variables are injected by the runtime:

| Variable | Description |
|----------|-------------|
| `BOHRIUM_OPENAPI_BASE_COM` | API base URL (auto-resolved by environment) |
| `BOHRIUM_ACCESS_KEY` | User's access key for authentication |
| `BOHRIUM_USER_ID` | Numeric user ID |

Check before proceeding:

```bash
echo "API_BASE=${BOHRIUM_OPENAPI_BASE_COM}"
echo "USER_ID=${BOHRIUM_USER_ID}"
echo "ACCESS_KEY=${BOHRIUM_ACCESS_KEY:+set}"
```

If any is empty, inform the user that the skill library is unavailable in the current session.

## Operations

### 1. List Skills

```bash
curl -s \
  -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_OPENAPI_BASE_COM}/openapi/v1/matmaster/users/${BOHRIUM_USER_ID}/skills" \
  | python3 -m json.tool
```

Response `.data` is an array of skill objects (newest first). Key fields:
- `name` — display name (parsed from SKILL.md in the package)
- `status` — `ready` / `uploading` / `failed`
- `byte_size`, `file_count` — package stats
- `created_at`, `updated_at`

### 2. Upload Skill

A skill directory must contain a `SKILL.md` at its root with YAML frontmatter:

```markdown
---
name: my-skill-name
description: "What this skill does"
---
# Skill documentation body...
```

#### Step 1 — Zip the skill directory

```bash
cd /path/to/parent && zip -r /tmp/skill_upload.zip my-skill-dir/
```

#### Step 2 — Upload the zip

```bash
curl -s -X POST \
  -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  -F "file=@/tmp/skill_upload.zip" \
  "${BOHRIUM_OPENAPI_BASE_COM}/openapi/v1/matmaster/users/${BOHRIUM_USER_ID}/skills/upload-zip" \
  | python3 -m json.tool
```

Response on success: `{ "code": 0, "data": { "object_key": "..." } }`

Max zip size: **100 MiB**.

#### Step 3 — Register the skill

Use the `object_key` from Step 2:

```bash
curl -s -X POST \
  -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"bundle_object_key": "<OBJECT_KEY_FROM_STEP_2>", "status": "ready"}' \
  "${BOHRIUM_OPENAPI_BASE_COM}/openapi/v1/matmaster/users/${BOHRIUM_USER_ID}/skills" \
  | python3 -m json.tool
```

The server parses `SKILL.md` from the zip to extract the display name automatically.

#### Cleanup

```bash
rm -f /tmp/skill_upload.zip
```

## Error Handling

All responses follow `{ "code": int, "data": ..., "msg": "..." }`. Check `code == 0` for success.

Common errors:
- HTTP 400: invalid or empty zip, `bundle_object_key` ownership mismatch
- HTTP 413: zip exceeds 100 MiB limit
- HTTP 503: OSS unavailable

## Workflow Tips

- Before uploading, verify the directory has a valid `SKILL.md` with frontmatter.
- After upload + register, call list to confirm the skill shows `status: ready`.
- The skill will be available to the user in their next agent session.
