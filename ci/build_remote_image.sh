#!/usr/bin/env bash
# Remote 节点镜像构建脚本（单环境）：
# 1. 列出名称含 matmaster 的镜像，并逐个调用 OpenAPI DELETE 删除（创建前清空）
# 2. 调用 Bohrium 创建镜像接口（Dockerfile.remote Base64），轮询至就绪得到 NEW_IMAGE_ID
# 3. 输出 NEW_IMAGE_ID 及 name:version，由 build_remote_image_all.sh 汇总写入 constant.py
#
# 依赖：CI 注入 BOHRIUM_ACCESS_KEY、BOHRIUM_PROJECT_ID、REMOTE_IMAGE_ENV（或由 CI_ENVIRONMENT_NAME 推导）

set -e

REMOTE_IMAGE_ENV="${REMOTE_IMAGE_ENV:-}"
if [[ -n "${CI_ENVIRONMENT_NAME:-}" ]]; then
  # GitLab environment: test / uat / production
  if [[ "$CI_ENVIRONMENT_NAME" == "production" ]]; then
    REMOTE_IMAGE_ENV="prod"
  else
    REMOTE_IMAGE_ENV="$CI_ENVIRONMENT_NAME"
  fi
fi
if [[ -z "$REMOTE_IMAGE_ENV" ]]; then
  log "ERROR: REMOTE_IMAGE_ENV or CI_ENVIRONMENT_NAME must be set (test/uat/prod)."
  exit 1
fi

if [[ -z "${BOHRIUM_ACCESS_KEY:-}" ]]; then
  log "ERROR: BOHRIUM_ACCESS_KEY is not set."
  exit 1
fi

case "$REMOTE_IMAGE_ENV" in
  test)  OPENAPI_V2_BASE="https://openapi.test.dp.tech/openapi/v2" ;;
  uat)  OPENAPI_V2_BASE="https://openapi.uat.dp.tech/openapi/v2" ;;
  prod) OPENAPI_V2_BASE="https://openapi.dp.tech/openapi/v2" ;;
  *)
    log "ERROR: REMOTE_IMAGE_ENV must be test, uat or prod, got: $REMOTE_IMAGE_ENV"
    exit 1
    ;;
esac

# 环境日志配色：test=cyan，uat=yellow，prod=red，其余默认
COLOR_RESET=$'\033[0m'
case "$REMOTE_IMAGE_ENV" in
  test) LOG_COLOR=$'\033[36m' ;;
  uat) LOG_COLOR=$'\033[33m' ;;
  prod) LOG_COLOR=$'\033[31m' ;;
  *) LOG_COLOR=$'\033[32m' ;;
esac
if [[ -n "${NO_COLOR:-}" ]]; then
  LOG_COLOR=''
  COLOR_RESET=''
fi
log() {
  local msg="$*"
  if [[ -n "$LOG_COLOR" ]]; then
    printf "%b%s%b\n" "$LOG_COLOR" "$msg" "$COLOR_RESET"
  else
    printf "%s\n" "$msg"
  fi
}

REPO_ROOT="${CI_PROJECT_DIR:-.}"
IMAGE_NAME_BASE="${REMOTE_IMAGE_NAME:-matmaster}"
IMAGE_NAME_QUERY="$IMAGE_NAME_BASE"

log "REMOTE_IMAGE_ENV=$REMOTE_IMAGE_ENV OPENAPI_V2_BASE=$OPENAPI_V2_BASE"

# 1) 列出名称含 matmaster 的镜像
LIST_URL="${OPENAPI_V2_BASE}/image/private?type=image&device=container&current=1&pageSize=20&page=1&name=${IMAGE_NAME_QUERY}"
RESP=$(curl -s -X GET "$LIST_URL" -H "accessKey: $BOHRIUM_ACCESS_KEY")
# prod 等环境若鉴权失败可能返回数字(如 401)或 HTML，先校验为 JSON 再解析
if ! echo "$RESP" | jq -e . >/dev/null 2>&1; then
  log "ERROR: List images API did not return valid JSON. Check BOHRIUM_ACCESS_KEY and API base URL."
  log "Raw response (first 500 chars): ${RESP:0:500}"
  exit 1
fi
CODE=$(echo "$RESP" | jq -r '.code // empty')
if [[ "$CODE" != "0" ]]; then
  log "WARN: list images returned code=$CODE, response: $RESP"
else
  log "List images (name contains ${IMAGE_NAME_QUERY}):"
  echo "$RESP" | jq -r '.data.items[]? | "  id=\(.id) name=\(.name) status=\(.status)"' 2>/dev/null || true
fi

# 2) 创建前删除列表中的旧镜像（OpenAPI DELETE，body 传 device: container）
for id in $(echo "$RESP" | jq -r '.data.items[]? | .id | tostring' 2>/dev/null); do
  [[ -z "$id" || "$id" == "null" ]] && continue
  DEL_URL="${OPENAPI_V2_BASE}/image/private/${id}"
  DEL_RESP=$(curl -s -w "\n%{http_code}" -X DELETE "$DEL_URL" \
    -H "accept: application/json, text/plain, */*" \
    -H "Content-Type: application/json" \
    -H "accessKey: $BOHRIUM_ACCESS_KEY" \
    --data-raw '{"device":"container"}' 2>/dev/null)
  HTTP_CODE=$(echo "$DEL_RESP" | tail -n1)
  BODY=$(echo "$DEL_RESP" | sed '$d')
  DEL_CODE=$(echo "$BODY" | jq -r '.code // empty')
  if [[ "$DEL_CODE" == "0" ]]; then
    log "Delete image id=$id: success (code=0)"
  else
    DEL_MSG=$(echo "$BODY" | jq -r '.error.msg // .error // .message // .msg // empty')
    log "Delete image id=$id: failed (code=$DEL_CODE) HTTP=$HTTP_CODE response: $BODY"
    [[ -n "$DEL_MSG" ]] && log "  -> $DEL_MSG"
  fi
  if [[ ! "$HTTP_CODE" =~ ^(200|204)$ ]]; then
    log "WARN: delete image id=$id returned HTTP $HTTP_CODE"
  fi
done

# 3) 调用 Bohrium 创建镜像接口，轮询就绪后得到 NEW_IMAGE_ID
if [[ -z "${BOHRIUM_PROJECT_ID:-}" ]]; then
  log "ERROR: BOHRIUM_PROJECT_ID is not set."
  exit 1
fi
DOCKERFILE_PATH="$REPO_ROOT/Dockerfile.remote"
if [[ ! -f "$DOCKERFILE_PATH" ]]; then
  log "ERROR: $DOCKERFILE_PATH not found."
  exit 1
fi
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
IMAGE_NAME="${IMAGE_NAME_BASE}"
if [[ -n "${REMOTE_IMAGE_VERSION:-}" ]]; then
  IMAGE_VERSION="${REMOTE_IMAGE_VERSION}"
elif [[ -n "${CI_COMMIT_SHORT_SHA:-}" ]]; then
  IMAGE_VERSION="${CI_COMMIT_SHORT_SHA}-${TIMESTAMP}"
else
  IMAGE_VERSION="${TIMESTAMP}"
fi
DOCKERFILE_B64=$(base64 -w 0 < "$DOCKERFILE_PATH" 2>/dev/null || base64 < "$DOCKERFILE_PATH")
CREATE_URL="${OPENAPI_V2_BASE}/image/private"
CREATE_JSON=$(jq -n \
  --arg device "container" \
  --argjson projectId "$BOHRIUM_PROJECT_ID" \
  --arg name "$IMAGE_NAME" \
  --arg version "$IMAGE_VERSION" \
  --argjson buildType 1 \
  --arg dockerFile "$DOCKERFILE_B64" \
  '{device: $device, projectId: $projectId, name: $name, version: $version, buildType: $buildType, dockerFile: $dockerFile}')
log "Creating image name=$IMAGE_NAME version=$IMAGE_VERSION via $CREATE_URL ..."
CREATE_RESP=$(curl -s -X POST "$CREATE_URL" \
  -H "accept: application/json, text/plain, */*" \
  -H "Content-Type: application/json" \
  -H "accessKey: $BOHRIUM_ACCESS_KEY" \
  --data-raw "$CREATE_JSON")
CREATE_CODE=$(echo "$CREATE_RESP" | jq -r '.code // empty')
if [[ "$CREATE_CODE" != "0" ]]; then
  log "ERROR: Create image failed. code=$CREATE_CODE response=$CREATE_RESP"
  exit 1
fi
NEW_IMAGE_ID=$(echo "$CREATE_RESP" | jq -r '.data.id // .data // empty')
if [[ -z "$NEW_IMAGE_ID" || "$NEW_IMAGE_ID" == "null" ]]; then
  log "ERROR: Create image returned no id. response=$CREATE_RESP"
  exit 1
fi
if ! [[ "$NEW_IMAGE_ID" =~ ^[0-9]+$ ]]; then
  log "ERROR: Create image returned non-numeric id. response=$CREATE_RESP"
  exit 1
fi
log "Image created id=$NEW_IMAGE_ID, waiting for status=2 (ready) ..."
POLL_INTERVAL=30
# 单环境镜像构建（apt + pip + VASPKIT）耗时长，等待 ready 需足够时间，原 15min 易超时
POLL_TIMEOUT=7200
DEADLINE=$(($(date +%s) + $POLL_TIMEOUT))
while true; do
  if [[ $(date +%s) -ge $DEADLINE ]]; then
    log "ERROR: Timeout waiting for image id=$NEW_IMAGE_ID to become ready (status=2)."
    exit 1
  fi
  LIST_RESP=$(curl -s -X GET "${OPENAPI_V2_BASE}/image/private?type=image&device=container&current=1&pageSize=20&page=1&name=${IMAGE_NAME_QUERY}" -H "accessKey: $BOHRIUM_ACCESS_KEY")
  STATUS=$(echo "$LIST_RESP" | jq -r --arg id "$NEW_IMAGE_ID" '.data.items[]? | select(.id == ($id | tonumber)) | .status | tostring' 2>/dev/null)
  if [[ "$STATUS" == "2" ]]; then
    log "Image id=$NEW_IMAGE_ID is ready (status=2)."
    break
  fi
  if [[ "$STATUS" == "3" ]]; then
    log "ERROR: Image id=$NEW_IMAGE_ID build failed (status=3). Do not update constant.py."
    exit 1
  fi
  log "  image id=$NEW_IMAGE_ID status=$STATUS (wait ${POLL_INTERVAL}s)"
  sleep "$POLL_INTERVAL"
done

IMAGE_NAME_WITH_VERSION="${IMAGE_NAME}:${IMAGE_VERSION}"
log "Image build completed node_env=${REMOTE_IMAGE_ENV} id=${NEW_IMAGE_ID} image=${IMAGE_NAME_WITH_VERSION}"
echo "REMOTE_IMAGE_RESULT_ENV=$REMOTE_IMAGE_ENV"
echo "REMOTE_IMAGE_RESULT_ID=$NEW_IMAGE_ID"
echo "REMOTE_IMAGE_RESULT_NAME=$IMAGE_NAME"
echo "REMOTE_IMAGE_RESULT_VERSION=$IMAGE_VERSION"
echo "REMOTE_IMAGE_RESULT_NAME_WITH_VERSION=$IMAGE_NAME_WITH_VERSION"
exit 0
