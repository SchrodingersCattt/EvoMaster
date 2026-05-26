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
| `BOHRIUM_BASE_URL` | API base URL for image/node endpoints |
| `BOHRIUM_ACCESS_KEY` | User's access key |
| `BOHRIUM_PROJECT_ID` | Default project ID |

```bash
echo "BASE=${BOHRIUM_BASE_URL}" "PROJECT_ID=${BOHRIUM_PROJECT_ID}" "AK=${BOHRIUM_ACCESS_KEY:+set}"
```

If any is empty → STOP. Inform user that image management is unavailable in the current session.

## Workflow

1. **List** — check current private images
2. **Build** — base64-encode Dockerfile, submit; poll list every 30s until new image shows `status == 2` (timeout after 10 min)
3. **Verify** — automatically after build succeeds; try debug node (Approach A: SSH), fall back to job submission (Approach B: Bohrium tool). Do NOT ask user whether to verify — always verify.
4. **Report** — show verification results with usage example, and suggest creating a skill if the image supports a specific workflow (see Post-Verification below)

Each step maps to a command in the API Reference below.

## API Reference

### List Private Images

```bash
curl -s -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_BASE_URL}/openapi/v2/image/private?device=container&type=image&page=1&pageSize=20" \
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

The `dockerfile` field must be **base64-encoded**. Plain text causes `decode err`.

Optionally validate first:

```bash
curl -s -X POST -H "accessKey: ${BOHRIUM_ACCESS_KEY}" -H "Content-Type: application/json" \
  -d '{"dockerfile": "<DOCKERFILE_CONTENT>"}' \
  "${BOHRIUM_BASE_URL}/openapi/v2/image/dockerfile/check"
```

Then submit (note: dockerfile is base64):

```bash
DOCKERFILE_B64=$(echo -n "FROM registry.dp.tech/dptech/ubuntu:22.04-py3.10
RUN pip install --no-cache-dir ase" | base64 -w 0)

curl -s -X POST -H "accessKey: ${BOHRIUM_ACCESS_KEY}" -H "Content-Type: application/json" \
  -d "{\"name\":\"<IMAGE_NAME>\",\"projectId\":${BOHRIUM_PROJECT_ID},\"device\":\"container\",\"desc\":\"<DESCRIPTION>\",\"buildType\":1,\"dockerfile\":\"${DOCKERFILE_B64}\"}" \
  "${BOHRIUM_BASE_URL}/openapi/v2/image/private" \
  | python3 -m json.tool
```

- `name` — image name (no registry prefix), e.g. `my-env:v1`
- `dockerfile` — base64-encoded Dockerfile content
- `desc` — optional description
- Base images must be from `registry.dp.tech`. Common base: `registry.dp.tech/dptech/ubuntu:22.04-py3.10-cuda12.1`

After submission, poll every 30s until the new image shows `status == 2`. Timeout after 10 min — inform user the build may have failed.

Poll command (filter by name):

```bash
curl -s -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_BASE_URL}/openapi/v2/image/private?device=container&type=image&page=1&pageSize=5" \
  | python3 -c "import sys,json; [print(f\"{i['name']} status={i['status']}\") for i in json.load(sys.stdin)['data']['items'] if '<IMAGE_NAME>' in i.get('name','')]"
```

### Debug Verification

Two approaches — try node first, fall back to job submission if unavailable.

#### Approach A: Debug Node (SSH access)

First query available machine resources:

```bash
curl -s -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_BASE_URL}/openapi/v1/node/resources" \
  | python3 -c "import sys,json; d=json.load(sys.stdin)['data']; [print(f\"  {m['label']} (value={m['value']})\") for m in d.get('cpuList',[])+d.get('gpuList',[])]"
```

Then create a node using an available SKU:

```bash
curl -s -X POST -H "accessKey: ${BOHRIUM_ACCESS_KEY}" -H "Content-Type: application/json" \
  -d '{
    "projectId": '"${BOHRIUM_PROJECT_ID}"',
    "name": "image-debug",
    "imageId": <IMAGE_ID>,
    "machineConfig": {"type": 0, "value": <SKU_VALUE>, "label": "<SKU_LABEL>"},
    "diskSize": 20
  }' \
  "${BOHRIUM_BASE_URL}/openapi/v1/node/add" \
  | python3 -m json.tool
```

Response gives `{"data": {"machineId": <MACHINE_ID>}}`. Wait ~30s for node to boot, then fetch SSH credentials:

```bash
curl -s -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_BASE_URL}/openapi/v1/node/<MACHINE_ID>" \
  | python3 -m json.tool
```

Connect using `domainName`, `nodeUser`, `nodePwd` from response:

```bash
ssh <nodeUser>@<domainName>
```

After verification, clean up:

```bash
curl -s -X POST -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_BASE_URL}/openapi/v1/node/stop/<MACHINE_ID>"
curl -s -X POST -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_BASE_URL}/openapi/v1/node/del/<MACHINE_ID>"
```

If node creation fails (resource unavailable), use Approach B.

#### Approach B: Job Submission (fallback)

Submit a test script using the Bohrium tool with the new image URL:

```
Bohrium(action="submit",
        image="<IMAGE_URL_FROM_LIST>",
        machine="c2_m4_cpu",
        input_dir="<dir_with_test_script>",
        cmd="bash test.sh > log 2>&1")
```

Write a `test.sh` that verifies key packages:

```bash
#!/bin/bash
python3 -c "import ase; print(f'ASE {ase.__version__}')"
python3 -c "import numpy; print(f'NumPy {numpy.__version__}')"
# add other checks as needed
```

Poll with `Bohrium(action="poll")`, then download and read log to confirm.

### Post-Verification

After verification passes, always:

1. **Show usage example** tailored to the image content:

```
镜像已验证通过，可用于提交任务：

Bohrium(action="submit",
        image="<IMAGE_URL>",
        machine="c2_m4_cpu",
        input_dir="<your_input_dir>",
        cmd="python3 your_script.py > log 2>&1")
```

2. **Suggest skill creation** if the image supports a repeatable workflow:

> 这个镜像可以作为一个可复用的计算环境。需要我帮你把它封装成一个 skill 吗？
> 这样以后用到这个环境时，agent 可以自动选用正确的镜像和参数。

If user agrees, load `skill-manager` skill to handle the upload.

## Error Handling

All responses: `{"code": int, "data": ..., "msg": "..."}`. Success = `code == 0`.

| Error | Cause | Fix |
|-------|-------|-----|
| `decode err` | Dockerfile not base64-encoded | Encode with `base64 -w 0` before submitting |
| `dockerfile err` | Invalid Dockerfile | Ensure FROM base exists in `registry.dp.tech` |
| `no permission` | Not image owner | Can only manage own images |
| `There is no resource` | SKU out of stock | Query `/node/resources` for available SKUs, or use Approach B |
| `record not found` | Wrong node ID | Use `machineId` from create response, not `nodeId` |
