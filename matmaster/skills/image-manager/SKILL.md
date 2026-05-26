---
name: image-manager
description: "Build and verify private container images on Bohrium. Use when: user wants to list private images, build a new image from Dockerfile, or spin up a debug node to verify a newly built image. NOT for: job submission, public image browsing, or general node management."
skill_type: operator
---

# Image Manager

Manage private container images on Bohrium: list, build, and verify via debug nodes.

## Capability Gate

- **STOP** if user wants to browse/search public images, submit compute jobs, or manage nodes for non-image purposes. Inform user this skill only covers private image lifecycle.

## Prerequisites

Runtime-injected environment variables:

| Variable | Description |
|----------|-------------|
| `BOHRIUM_OPENAPI_BASE_COM` | API base URL |
| `BOHRIUM_ACCESS_KEY` | User's access key |
| `BOHRIUM_PROJECT_ID` | Default project ID |

```bash
echo "API_BASE=${BOHRIUM_OPENAPI_BASE_COM}" "PROJECT_ID=${BOHRIUM_PROJECT_ID}" "AK=${BOHRIUM_ACCESS_KEY:+set}"
```

If any is empty → STOP. Inform user that image management is unavailable in the current session.

## Workflow

1. **List** — check current private images
2. **Build** — submit Dockerfile; poll list every 30s until new image shows `status == 2` (timeout after 10 min)
3. **Verify** (optional) — create debug node with the new image, SSH in, confirm environment, then clean up node

Each step maps to a command in the API Reference below.

## API Reference

### List Private Images

```bash
curl -s -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_OPENAPI_BASE_COM}/openapi/v2/image/private?device=container&type=image&page=1&pageSize=20" \
  | python3 -m json.tool
```

Response `.data.items` key fields:

| Field | Description |
|-------|-------------|
| `id` | Image ID (used as `imageId` when creating nodes) |
| `name` | Name with tag, e.g. `my-env:v1` |
| `url` | Full registry URL |
| `size` | Image size |
| `status` | `2` = ready, other = building/failed |
| `createTime` | Creation timestamp |

### Build Image

Optionally validate first:

```bash
curl -s -X POST -H "accessKey: ${BOHRIUM_ACCESS_KEY}" -H "Content-Type: application/json" \
  -d '{"dockerfile": "<DOCKERFILE_CONTENT>"}' \
  "${BOHRIUM_OPENAPI_BASE_COM}/openapi/v2/image/dockerfile/check"
```

Then submit:

```bash
curl -s -X POST -H "accessKey: ${BOHRIUM_ACCESS_KEY}" -H "Content-Type: application/json" \
  -d '{
    "name": "<IMAGE_NAME>",
    "projectId": '"${BOHRIUM_PROJECT_ID}"',
    "device": "container",
    "desc": "<DESCRIPTION>",
    "buildType": 1,
    "dockerfile": "<DOCKERFILE_CONTENT>"
  }' \
  "${BOHRIUM_OPENAPI_BASE_COM}/openapi/v2/image/private" \
  | python3 -m json.tool
```

- `name` — image name (no registry prefix)
- `dockerfile` — Dockerfile content, newlines as `\n`
- `desc` — optional description
- Base images must be from `registry.dp.tech`. Common base: `registry.dp.tech/dptech/ubuntu:22.04-py3.10-cuda12.1`

After submission, poll every 30s until the new image shows `status == 2`. Timeout after 10 min — inform user the build may have failed.

Poll command (filter by name):

```bash
curl -s -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_OPENAPI_BASE_COM}/openapi/v2/image/private?device=container&type=image&page=1&pageSize=5" \
  | python3 -c "import sys,json; [print(f\"{i['name']} status={i['status']}\") for i in json.load(sys.stdin)['data']['items'] if '<IMAGE_NAME>' in i.get('name','')]"
```

### Debug Verification

Create a minimal node with the new image:

```bash
curl -s -X POST -H "accessKey: ${BOHRIUM_ACCESS_KEY}" -H "Content-Type: application/json" \
  -d '{
    "projectId": '"${BOHRIUM_PROJECT_ID}"',
    "name": "image-debug",
    "imageId": <IMAGE_ID>,
    "machineConfig": {"type": 0, "value": 388, "label": "c2_m4_cpu"},
    "diskSize": 20
  }' \
  "${BOHRIUM_OPENAPI_BASE_COM}/openapi/v1/node/add" \
  | python3 -m json.tool
```

Response gives `{"data": {"machineId": <MACHINE_ID>}}`. Wait ~30s for node to boot, then fetch SSH credentials:

```bash
curl -s -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_OPENAPI_BASE_COM}/openapi/v1/node/<MACHINE_ID>" \
  | python3 -m json.tool
```

Connect using `domainName`, `nodeUser`, `nodePwd` from response:

```bash
ssh <nodeUser>@<domainName>
```

After verification, clean up:

```bash
curl -s -X POST -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_OPENAPI_BASE_COM}/openapi/v1/node/stop/<MACHINE_ID>"
curl -s -X POST -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_OPENAPI_BASE_COM}/openapi/v1/node/del/<MACHINE_ID>"
```

## Error Handling

All responses: `{"code": int, "data": ..., "msg": "..."}`. Success = `code == 0`.

| Error | Cause | Fix |
|-------|-------|-----|
| `dockerfile err` | Invalid Dockerfile | Ensure FROM base exists in `registry.dp.tech` |
| `no permission` | Not image owner | Can only manage own images |
| `There is no resource` | SKU out of stock | Try different `machineConfig` value |
| `record not found` | Wrong node ID | Use `machineId` from create response, not `nodeId` |
