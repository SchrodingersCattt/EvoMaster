# Approach A: Debug Node (SSH access)

Create a node with the new image for interactive SSH verification.

## 1. Query Available Resources

```bash
curl -s -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_BASE_URL}/openapi/v1/node/resources" \
  | python3 -c "import sys,json; d=json.load(sys.stdin)['data']; [print(f\"  {m['label']} (value={m['value']})\") for m in d.get('cpuList',[])+d.get('gpuList',[])]"
```

## 2. Create Node

Use an available SKU from step 1:

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

## 3. Get SSH Credentials

Response gives `{"data": {"machineId": <MACHINE_ID>}}`. Wait ~30s for node to boot, then:

```bash
curl -s -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_BASE_URL}/openapi/v1/node/<MACHINE_ID>" \
  | python3 -m json.tool
```

Connect using `domainName`, `nodeUser`, `nodePwd` from response:

```bash
ssh <nodeUser>@<domainName>
```

## 4. Clean Up

After verification, stop and delete:

```bash
curl -s -X POST -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_BASE_URL}/openapi/v1/node/stop/<MACHINE_ID>"
curl -s -X POST -H "accessKey: ${BOHRIUM_ACCESS_KEY}" \
  "${BOHRIUM_BASE_URL}/openapi/v1/node/del/<MACHINE_ID>"
```
