#!/usr/bin/env bash
# Remote 节点镜像构建流水线脚本：
# 1. 列出名称含 matmaster 的镜像，并逐个调用 OpenAPI DELETE 删除（创建前清空）
# 2. 调用 Bohrium 创建镜像接口（Dockerfile.remote Base64），轮询至就绪得到 NEW_IMAGE_ID
# 3. 用 NEW_IMAGE_ID 更新 src/utils/constant.py 中当前环境的 BOHRIUM_ENV_DEFAULT_IMAGE_IDS
# 4. 提交并 push（仅改 constant.py，不会再次触发 Dockerfile.remote 的 rules）
#
# 依赖：CI 注入 BOHRIUM_ACCESS_KEY、BOHRIUM_PROJECT_ID、REMOTE_IMAGE_ENV（或由 CI_ENVIRONMENT_NAME 推导）
# 可选：commit 作者 = 触发流水线用户（GITLAB_USER_*），否则默认 zhouh@dp.tech/zhouh

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
  echo "ERROR: REMOTE_IMAGE_ENV or CI_ENVIRONMENT_NAME must be set (test/uat/prod)."
  exit 1
fi

if [[ -z "${BOHRIUM_ACCESS_KEY:-}" ]]; then
  echo "ERROR: BOHRIUM_ACCESS_KEY is not set."
  exit 1
fi

case "$REMOTE_IMAGE_ENV" in
  test)  OPENAPI_V2_BASE="https://openapi.test.dp.tech/openapi/v2" ;;
  uat)  OPENAPI_V2_BASE="https://openapi.uat.dp.tech/openapi/v2" ;;
  prod) OPENAPI_V2_BASE="https://openapi.dp.tech/openapi/v2" ;;
  *)
    echo "ERROR: REMOTE_IMAGE_ENV must be test, uat or prod, got: $REMOTE_IMAGE_ENV"
    exit 1
    ;;
esac

REPO_ROOT="${CI_PROJECT_DIR:-.}"
CONSTANT_FILE="$REPO_ROOT/src/utils/constant.py"
IMAGE_NAME_QUERY="${REMOTE_IMAGE_NAME:-matmaster}"

echo "REMOTE_IMAGE_ENV=$REMOTE_IMAGE_ENV OPENAPI_V2_BASE=$OPENAPI_V2_BASE"

# 1) 列出名称含 matmaster 的镜像
LIST_URL="${OPENAPI_V2_BASE}/image/private?type=image&device=container&current=1&pageSize=20&page=1&name=${IMAGE_NAME_QUERY}"
RESP=$(curl -s -X GET "$LIST_URL" -H "accessKey: $BOHRIUM_ACCESS_KEY")
# prod 等环境若鉴权失败可能返回数字(如 401)或 HTML，先校验为 JSON 再解析
if ! echo "$RESP" | jq -e . >/dev/null 2>&1; then
  echo "ERROR: List images API did not return valid JSON. Check BOHRIUM_ACCESS_KEY and API base URL."
  echo "Raw response (first 500 chars): ${RESP:0:500}"
  exit 1
fi
CODE=$(echo "$RESP" | jq -r '.code // empty')
if [[ "$CODE" != "0" ]]; then
  echo "WARN: list images returned code=$CODE, response: $RESP"
else
  echo "List images (name contains ${IMAGE_NAME_QUERY}):"
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
    echo "Delete image id=$id: success (code=0)"
  else
    DEL_MSG=$(echo "$BODY" | jq -r '.error.msg // .error // .message // .msg // empty')
    echo "Delete image id=$id: failed (code=$DEL_CODE) HTTP=$HTTP_CODE response: $BODY"
    [[ -n "$DEL_MSG" ]] && echo "  -> $DEL_MSG"
  fi
  if [[ ! "$HTTP_CODE" =~ ^(200|204)$ ]]; then
    echo "WARN: delete image id=$id returned HTTP $HTTP_CODE"
  fi
done

# 3) 调用 Bohrium 创建镜像接口，轮询就绪后得到 NEW_IMAGE_ID
if [[ -z "${BOHRIUM_PROJECT_ID:-}" ]]; then
  echo "ERROR: BOHRIUM_PROJECT_ID is not set."
  exit 1
fi
DOCKERFILE_PATH="$REPO_ROOT/Dockerfile.remote"
if [[ ! -f "$DOCKERFILE_PATH" ]]; then
  echo "ERROR: $DOCKERFILE_PATH not found."
  exit 1
fi
IMAGE_NAME="${REMOTE_IMAGE_NAME:-matmaster}"
IMAGE_VERSION="${REMOTE_IMAGE_VERSION:-$(date +%Y-%m%d-%H%M)}"
if [[ -n "${CI_COMMIT_SHORT_SHA:-}" ]]; then
  IMAGE_VERSION="${CI_COMMIT_SHORT_SHA}"
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
echo "Creating image name=$IMAGE_NAME version=$IMAGE_VERSION via $CREATE_URL ..."
CREATE_RESP=$(curl -s -X POST "$CREATE_URL" \
  -H "accept: application/json, text/plain, */*" \
  -H "Content-Type: application/json" \
  -H "accessKey: $BOHRIUM_ACCESS_KEY" \
  --data-raw "$CREATE_JSON")
CREATE_CODE=$(echo "$CREATE_RESP" | jq -r '.code // empty')
if [[ "$CREATE_CODE" != "0" ]]; then
  echo "ERROR: Create image failed. code=$CREATE_CODE response=$CREATE_RESP"
  exit 1
fi
NEW_IMAGE_ID=$(echo "$CREATE_RESP" | jq -r '.data.id // .data // empty')
if [[ -z "$NEW_IMAGE_ID" || "$NEW_IMAGE_ID" == "null" ]]; then
  echo "ERROR: Create image returned no id. response=$CREATE_RESP"
  exit 1
fi
if ! [[ "$NEW_IMAGE_ID" =~ ^[0-9]+$ ]]; then
  echo "ERROR: Create image returned non-numeric id. response=$CREATE_RESP"
  exit 1
fi
echo "Image created id=$NEW_IMAGE_ID, waiting for status=2 (ready) ..."
POLL_INTERVAL=30
POLL_TIMEOUT=600
DEADLINE=$(($(date +%s) + $POLL_TIMEOUT))
while true; do
  if [[ $(date +%s) -ge $DEADLINE ]]; then
    echo "ERROR: Timeout waiting for image id=$NEW_IMAGE_ID to become ready (status=2)."
    exit 1
  fi
  LIST_RESP=$(curl -s -X GET "${OPENAPI_V2_BASE}/image/private?type=image&device=container&current=1&pageSize=20&page=1&name=${IMAGE_NAME_QUERY}" -H "accessKey: $BOHRIUM_ACCESS_KEY")
  STATUS=$(echo "$LIST_RESP" | jq -r --arg id "$NEW_IMAGE_ID" '.data.items[]? | select(.id == ($id | tonumber)) | .status | tostring' 2>/dev/null)
  if [[ "$STATUS" == "2" ]]; then
    echo "Image id=$NEW_IMAGE_ID is ready (status=2)."
    break
  fi
  if [[ "$STATUS" == "3" ]]; then
    echo "ERROR: Image id=$NEW_IMAGE_ID build failed (status=3). Do not update constant.py."
    exit 1
  fi
  echo "  image id=$NEW_IMAGE_ID status=$STATUS (wait ${POLL_INTERVAL}s)"
  sleep "$POLL_INTERVAL"
done

# BUILD_ONLY=1 时仅输出 NEW_IMAGE_ID 供 build_remote_image_all.sh 聚合，不写 constant.py 不 push
if [[ -n "${BUILD_ONLY:-}" ]]; then
  echo "BUILD_ONLY_NEW_IMAGE_ID=$NEW_IMAGE_ID"
  exit 0
fi

# 4) 更新 constant.py 并 push，保证仓库持久为新镜像 ID；本流水线不跑 docker-build（见 .gitlab-ci rules），由 push 触发的下一条流水线再 build/deploy
if [[ ! -f "$CONSTANT_FILE" ]]; then
  echo "ERROR: $CONSTANT_FILE not found."
  exit 1
fi
sed -i.bak "s/'${REMOTE_IMAGE_ENV}': [0-9]*/'${REMOTE_IMAGE_ENV}': ${NEW_IMAGE_ID}/" "$CONSTANT_FILE"
ESCAPED_IMAGE_NAME=$(printf '%s' "$IMAGE_NAME" | sed -e 's/\\/\\\\/g' -e 's/&/\\&/g' -e 's/|/\\|/g')
sed -i.bak "s|'${REMOTE_IMAGE_ENV}': '[^']*'|'${REMOTE_IMAGE_ENV}': '${ESCAPED_IMAGE_NAME}'|" "$CONSTANT_FILE"
rm -f "${CONSTANT_FILE}.bak"
echo "Updated BOHRIUM_ENV_DEFAULT_IMAGE_IDS['${REMOTE_IMAGE_ENV}'] = $NEW_IMAGE_ID"
echo "Updated BOHRIUM_ENV_DEFAULT_IMAGE_NAMES['${REMOTE_IMAGE_ENV}'] = ${IMAGE_NAME}"

# 5) 提交并 push，后续任意 commit 都会用新镜像 ID
cd "$REPO_ROOT"
git config user.email "${GITLAB_USER_EMAIL:-zhouh@dp.tech}"
git config user.name "${GITLAB_USER_NAME:-zhouh}"
git add "$CONSTANT_FILE"
if git diff --cached --quiet; then
  echo "No change in constant.py (already ${REMOTE_IMAGE_ENV}=${NEW_IMAGE_ID})."
  exit 0
fi
git commit -m "chore(remote-image): set BOHRIUM_ENV_DEFAULT_IMAGE_IDS['${REMOTE_IMAGE_ENV}'] to ${NEW_IMAGE_ID} [skip ci]"
BRANCH="${CI_COMMIT_BRANCH:-${CI_COMMIT_REF_NAME:-$(git branch --show-current)}}"
if [[ -z "$BRANCH" ]]; then
  echo "ERROR: Could not determine branch (CI_COMMIT_BRANCH/CI_COMMIT_REF_NAME unset)."
  exit 1
fi
if [[ -n "${CI_SERVER_HOST:-}" && -n "${CI_PROJECT_PATH:-}" ]]; then
  if [[ -n "${GIT_PUSH_TOKEN:-}" ]]; then
    git push "https://oauth2:${GIT_PUSH_TOKEN}@${CI_SERVER_HOST}/${CI_PROJECT_PATH}.git" "HEAD:${BRANCH}"
  elif [[ -n "${CI_JOB_TOKEN:-}" ]]; then
    git push "https://gitlab-ci-token:${CI_JOB_TOKEN}@${CI_SERVER_HOST}/${CI_PROJECT_PATH}.git" "HEAD:${BRANCH}"
  else
    echo "ERROR: Set GIT_PUSH_TOKEN or ensure CI_JOB_TOKEN has push permission."
    exit 1
  fi
else
  git push origin "HEAD:${BRANCH}"
fi
echo "Done: pushed BOHRIUM_ENV_DEFAULT_IMAGE_IDS['${REMOTE_IMAGE_ENV}']=${NEW_IMAGE_ID}; next pipeline will build/deploy with it."
