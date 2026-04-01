#!/usr/bin/env bash
# 在 docker-build 产出的应用镜像内跑 pre-commit：使用镜像内预装的 PRE_COMMIT_HOME，
# 工作目录挂载为当前 CI 检出，与 sync-question-catalog 一类「先 build 再 docker run」一致。
set -euo pipefail

IMAGE="${1:?用法: $0 <镜像名>}"
: "${CI_PROJECT_DIR:?}"

docker run --rm \
  -e CI_PIPELINE_SOURCE \
  -e CI_MERGE_REQUEST_DIFF_BASE_SHA \
  -e CI_COMMIT_SHA \
  -e CI_COMMIT_BEFORE_SHA \
  -e CI_DEFAULT_BRANCH \
  -e GITLAB_CI \
  -v "${CI_PROJECT_DIR}:${CI_PROJECT_DIR}" \
  -w "${CI_PROJECT_DIR}" \
  "${IMAGE}" \
  bash -lc "export PRE_COMMIT_HOME=/app/.cache/pre-commit && export PATH=/app/.venv/bin:\$PATH && exec bash '${CI_PROJECT_DIR}/ci/run_pre_commit_ci.sh'"
