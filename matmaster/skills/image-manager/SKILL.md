---
name: image-manager
description: "Build and verify private container images on Bohrium. Use when: user wants to list private images, build a new image from Dockerfile, or spin up a debug node to verify a newly built image. NOT for: job submission, public image browsing, or general node management."
skill_type: operator
---

# Image Manager

Manage private container images on Bohrium: list, build, and verify via debug nodes.

## Prerequisites

The following environment variables are injected by the runtime:

| Variable | Description |
|----------|-------------|
| `BOHRIUM_OPENAPI_BASE_COM` | API base URL (auto-resolved by environment) |
| `BOHRIUM_ACCESS_KEY` | User's access key for authentication |
| `BOHRIUM_PROJECT_ID` | User's default project ID |

Check before proceeding:

```bash
echo "API_BASE=${BOHRIUM_OPENAPI_BASE_COM}"
echo "PROJECT_ID=${BOHRIUM_PROJECT_ID}"
echo "ACCESS_KEY=${BOHRIUM_ACCESS_KEY:+set}"
```

If any is empty, inform the user that image management is unavailable in the current session.

## Operations

### 1. List Private Images

```bash
curl -s \
  -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_OPENAPI_BASE_COM}/openapi/v2/image/private?device=container&type=private&page=1&pageSize=20" \
  | python3 -m json.tool
```

Response `.data.items` is an array of image objects. Key fields:
- `id` — image ID (used for node creation)
- `name` — image name with tag (e.g. `my-env:v1`)
- `url` — full registry URL (e.g. `registry.dp.tech/dptech/dp/native/prod-xxx/my-env:v1`)
- `size` — image size
- `status` — 2 = ready
- `desc` — description
- `createTime`

### 2. Build Image (from Dockerfile)

```bash
curl -s -X POST \
  -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  -H "Content-Type: application/json" \
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

**Parameters:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `name` | yes | Image name (without registry prefix) |
| `projectId` | yes | From `$BOHRIUM_PROJECT_ID` |
| `device` | yes | Always `container` |
| `desc` | no | Human-readable description |
| `buildType` | yes | Always `1` (Dockerfile) |
| `dockerfile` | yes | Dockerfile content as a string (newlines as `\n`) |

**Dockerfile tips:**
- Base images must exist in `registry.dp.tech`. Common bases: `registry.dp.tech/dptech/ubuntu:22.04-py3.10-cuda12.1`
- Build is async on server side. After creation, poll the image list until `status == 2`.

### 3. Check Dockerfile Validity

Before building, optionally validate:

```bash
curl -s -X POST \
  -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"dockerfile": "<DOCKERFILE_CONTENT>"}' \
  "${BOHRIUM_OPENAPI_BASE_COM}/openapi/v2/image/dockerfile/check" \
  | python3 -m json.tool
```

### 4. Debug: Create a Node with the New Image

After building, spin up a lightweight node to verify the image:

```bash
curl -s -X POST \
  -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  -H "Content-Type: application/json" \
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

Response: `{"code": 0, "data": {"machineId": <MACHINE_ID>}}`

Then fetch node details to get SSH credentials:

```bash
curl -s \
  -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_OPENAPI_BASE_COM}/openapi/v1/node/<MACHINE_ID>" \
  | python3 -m json.tool
```

Key fields for SSH:
- `domainName` — SSH host (e.g. `cset1427218.bohrium.tech`)
- `nodeUser` — usually `root`
- `nodePwd` — password

Connect: `ssh <nodeUser>@<domainName>` (password from response).

### 5. Debug: Clean Up Node

After verification, stop and delete the debug node:

```bash
# Stop
curl -s -X POST \
  -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_OPENAPI_BASE_COM}/openapi/v1/node/stop/<MACHINE_ID>" \
  | python3 -m json.tool

# Delete
curl -s -X POST \
  -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_OPENAPI_BASE_COM}/openapi/v1/node/del/<MACHINE_ID>" \
  | python3 -m json.tool
```

## Workflow

Typical image development cycle:

1. **List** existing images to check current state
2. **Check** Dockerfile validity (optional)
3. **Build** — submit Dockerfile, poll list until `status == 2`
4. **Verify** — create debug node with the new image ID, SSH in, confirm packages/tools are present
5. **Clean up** — stop and delete the debug node

## Error Handling

All responses follow `{"code": int, "data": ..., "msg": "..."}`. Check `code == 0` for success.

| Error | Cause | Fix |
|-------|-------|-----|
| `dockerfile err` | Invalid Dockerfile | Ensure FROM base exists in `registry.dp.tech` |
| `no permission` | Not image owner | Can only manage own images |
| `There is no resource for the selected machine` | SKU out of stock | Try a different `machineConfig` value |
| Node detail returns `record not found` | Wrong ID | Use `machineId` from create response, not `nodeId` |
