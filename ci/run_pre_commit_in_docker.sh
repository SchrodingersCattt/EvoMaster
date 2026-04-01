#!/usr/bin/env bash
# 在 docker-build 产出的应用镜像内跑 pre-commit：使用镜像内预装的 PRE_COMMIT_HOME。
# 远程 DOCKER_HOST（如 tcp://docker-build-service）上 -v 挂载的是「远端守护进程所在机器」的路径，
# Runner 上的 CI_PROJECT_DIR 在容器里不存在；故用 docker cp 将检出拷入容器（与同流水线 test job 的 docker cp 一致）。
set -euo pipefail

IMAGE="${1:?用法: $0 <镜像名>}"
: "${CI_PROJECT_DIR:?}"

NAME="precommit-${CI_PIPELINE_ID:-local}-$$"
cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker create --name "$NAME" "$IMAGE" sleep 3600 >/dev/null
docker cp "${CI_PROJECT_DIR}/." "${NAME}:/tmp/ci-workspace"
docker start "$NAME" >/dev/null

docker exec \
  -e CI_PIPELINE_SOURCE \
  -e CI_MERGE_REQUEST_DIFF_BASE_SHA \
  -e CI_COMMIT_SHA \
  -e CI_COMMIT_BEFORE_SHA \
  -e CI_DEFAULT_BRANCH \
  -e GITLAB_CI \
  -w /tmp/ci-workspace \
  "$NAME" \
  bash -c 'export PRE_COMMIT_HOME=/app/.cache/pre-commit PATH="/app/.venv/bin:$PATH" && exec bash ci/run_pre_commit_ci.sh'
