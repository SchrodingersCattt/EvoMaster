#!/usr/bin/env bash
# 一次构建 test / uat / prod 三环境 Remote 节点镜像（并行），并一次性更新 constant.py 后 push。
# 触发方式：推送到分支 remote-image（见 .gitlab-ci.yml build-remote-image:all）。
#
# 依赖：CI 中配置 BOHRIUM_ACCESS_KEY_TEST、BOHRIUM_PROJECT_ID_TEST、BOHRIUM_ACCESS_KEY_UAT、
#       BOHRIUM_PROJECT_ID_UAT、BOHRIUM_ACCESS_KEY_PROD、BOHRIUM_PROJECT_ID_PROD（与各环境一致即可）。

set -e

REPO_ROOT="${CI_PROJECT_DIR:-.}"
CONSTANT_FILE="$REPO_ROOT/src/utils/constant.py"
SCRIPT_DIR="${CI_PROJECT_DIR:-.}/ci"
PARALLEL_DIR="${PARALLEL_DIR:-/tmp/remote_image_parallel}"
mkdir -p "$PARALLEL_DIR"

declare -A ID_BY_ENV

# 先校验三环境变量都已配置
for env in test uat prod; do
  key_var="BOHRIUM_ACCESS_KEY_$(echo "$env" | tr 'a-z' 'A-Z')"
  proj_var="BOHRIUM_PROJECT_ID_$(echo "$env" | tr 'a-z' 'A-Z')"
  if [[ -z "${!key_var:-}" || -z "${!proj_var:-}" ]]; then
    echo "ERROR: $key_var or $proj_var is not set."
    exit 1
  fi
done

# 并行跑三环境，用 tee 实时打 log（带 [test]/[uat]/[prod] 前缀），并写入临时 log 供后续解析 ID
echo "=== Building remote images in parallel (test, uat, prod) ==="
for env in test uat prod; do
  (
    key_var="BOHRIUM_ACCESS_KEY_$(echo "$env" | tr 'a-z' 'A-Z')"
    proj_var="BOHRIUM_PROJECT_ID_$(echo "$env" | tr 'a-z' 'A-Z')"
    export BOHRIUM_ACCESS_KEY="${!key_var}"
    export BOHRIUM_PROJECT_ID="${!proj_var}"
    stdbuf -oL -eL BUILD_ONLY=1 REMOTE_IMAGE_ENV="$env" "$SCRIPT_DIR/build_remote_image.sh" 2>&1 \
      | while IFS= read -r line; do echo "[$env] $line"; done \
      | tee "$PARALLEL_DIR/${env}.log"
  ) &
done
wait

# 从 log 中解析 NEW_IMAGE_ID，任一失败则打印该环境 log 并退出
for env in test uat prod; do
  id=$(grep 'BUILD_ONLY_NEW_IMAGE_ID=' "$PARALLEL_DIR/${env}.log" 2>/dev/null | sed -n 's/.*BUILD_ONLY_NEW_IMAGE_ID=\([0-9]*\).*/\1/p' | head -1)
  if [[ -z "$id" || ! "$id" =~ ^[0-9]+$ ]]; then
    echo "ERROR: Could not get NEW_IMAGE_ID for env=$env."
    logfile="$PARALLEL_DIR/${env}.log"
    if [[ -s "$logfile" ]]; then
      echo "Build log (last 50 lines):"
      tail -50 "$logfile"
    else
      echo "Build log file empty or missing. ls $PARALLEL_DIR:"
      ls -la "$PARALLEL_DIR/" 2>/dev/null || true
    fi
    exit 1
  fi
  ID_BY_ENV[$env]=$id
  echo "  -> env=$env NEW_IMAGE_ID=$id"
done

echo "=== Updating $CONSTANT_FILE with test=${ID_BY_ENV[test]} uat=${ID_BY_ENV[uat]} prod=${ID_BY_ENV[prod]} ==="
for env in test uat prod; do
  sed -i.bak "s/'${env}': [0-9]*/'${env}': ${ID_BY_ENV[$env]}/" "$CONSTANT_FILE"
done
rm -f "${CONSTANT_FILE}.bak"

cd "$REPO_ROOT"
git config user.email "${GITLAB_USER_EMAIL:-zhouh@dp.tech}"
git config user.name "${GITLAB_USER_NAME:-zhouh}"
git add "$CONSTANT_FILE"
if git diff --cached --quiet; then
  echo "No change in constant.py (all envs already up to date)."
  exit 0
fi
git commit -m "chore(remote-image): set BOHRIUM_ENV_DEFAULT_IMAGE_IDS test=${ID_BY_ENV[test]} uat=${ID_BY_ENV[uat]} prod=${ID_BY_ENV[prod]}"
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
echo "Done: pushed BOHRIUM_ENV_DEFAULT_IMAGE_IDS test=${ID_BY_ENV[test]} uat=${ID_BY_ENV[uat]} prod=${ID_BY_ENV[prod]}."
