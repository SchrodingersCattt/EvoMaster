---
name: skill-manager
description: "List, upload, and delete user skills in the MatMaster skill library. Use this when the user wants to save a workflow as a reusable skill, check existing skills, or remove one."
skill_type: operator
---

# Skill Manager

Manage the user's personal skill library via the MatMaster Tools Server API.

## Prerequisites

- **API Base**: `${MATMASTER_TOOLS_SERVER}` (environment variable, typically `https://matmaster-tools-server.bohrium.com`)
- **User ID**: `${MATMASTER_USER_ID}` (environment variable, set by the runtime)
- All requests require header: `X-User-Id: ${MATMASTER_USER_ID}`

Check that both variables are set before proceeding:

```bash
echo "TOOLS_SERVER=${MATMASTER_TOOLS_SERVER}"
echo "USER_ID=${MATMASTER_USER_ID}"
```

If either is empty, inform the user that the skill library is unavailable in the current session.

## Operations

### 1. List Skills

```bash
curl -s -X GET \
  "${MATMASTER_TOOLS_SERVER}/api/v1/users/${MATMASTER_USER_ID}/skills" \
  -H "X-User-Id: ${MATMASTER_USER_ID}" | python3 -m json.tool
```

Response `.data` is an array of skill objects (newest first). Key fields:
- `id` — skill ID (for delete)
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

#### Step 2 — Upload the zip (server-side)

```bash
curl -s -X POST \
  "${MATMASTER_TOOLS_SERVER}/api/v1/users/${MATMASTER_USER_ID}/skills/upload-zip" \
  -H "X-User-Id: ${MATMASTER_USER_ID}" \
  -F "file=@/tmp/skill_upload.zip" | python3 -m json.tool
```

Response on success: `{ "code": 0, "data": { "object_key": "..." } }`

Max zip size: **100 MiB**.

#### Step 3 — Register the skill

Use the `object_key` from Step 2:

```bash
curl -s -X POST \
  "${MATMASTER_TOOLS_SERVER}/api/v1/users/${MATMASTER_USER_ID}/skills" \
  -H "X-User-Id: ${MATMASTER_USER_ID}" \
  -H "Content-Type: application/json" \
  -d '{"bundle_object_key": "<OBJECT_KEY_FROM_STEP_2>", "status": "ready"}' | python3 -m json.tool
```

The server parses `SKILL.md` from the zip to extract the display name automatically.

Response on success returns the complete skill record with `id`, `name`, `status`, `artifact_id`, etc.

#### Cleanup

```bash
rm -f /tmp/skill_upload.zip
```

### 3. Delete Skill

```bash
curl -s -X DELETE \
  "${MATMASTER_TOOLS_SERVER}/api/v1/users/${MATMASTER_USER_ID}/skills/<SKILL_ID>" \
  -H "X-User-Id: ${MATMASTER_USER_ID}" | python3 -m json.tool
```

## Error Handling

All responses follow `{ "code": int, "data": ..., "msg": "..." }`. Check `code == 0` for success.

Common errors:
- HTTP 400: invalid or empty zip, `bundle_object_key` ownership mismatch
- HTTP 404: skill not found (on delete)
- HTTP 413: zip exceeds 100 MiB limit
- HTTP 503: OSS unavailable

## Workflow Tips

- Before uploading, verify the directory has a valid `SKILL.md` with frontmatter.
- After upload + register, call list to confirm the skill shows `status: ready`.
- The skill will be available to the user in their next agent session.
