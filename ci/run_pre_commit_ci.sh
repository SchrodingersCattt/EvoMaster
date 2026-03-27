#!/usr/bin/env bash
# GitLab CI：仅对本次 diff 内变更文件跑 pre-commit。
# 依赖 CI 里单独 pip install 的 pre-commit；各 hook 由 pre-commit 按 .pre-commit-config.yaml 自建环境，无需 uv sync 项目依赖。
set -euo pipefail

collect_changed() {
  # 合并请求流水线：相对 MR 目标分支的基线
  if [ "${CI_PIPELINE_SOURCE:-}" = "merge_request_event" ] && [ -n "${CI_MERGE_REQUEST_DIFF_BASE_SHA:-}" ]; then
    git diff --name-only --diff-filter=d "${CI_MERGE_REQUEST_DIFF_BASE_SHA}" "${CI_COMMIT_SHA}" 2>/dev/null || true
    return 0
  fi
  # 普通 push：相对上一提交
  if [ -n "${CI_COMMIT_BEFORE_SHA:-}" ] && [ "${CI_COMMIT_BEFORE_SHA}" != "0000000000000000000000000000000000000000" ]; then
    git diff --name-only --diff-filter=d "${CI_COMMIT_BEFORE_SHA}" "${CI_COMMIT_SHA}" 2>/dev/null || true
    return 0
  fi
  # 新分支首推等：与默认分支求 merge-base 后 diff
  local db="${CI_DEFAULT_BRANCH:-main}"
  git fetch origin "${db}" 2>/dev/null || true
  if ! git rev-parse "origin/${db}" >/dev/null 2>&1; then
    return 0
  fi
  local base
  base=$(git merge-base HEAD "origin/${db}" 2>/dev/null || true)
  if [ -z "${base:-}" ]; then
    return 0
  fi
  git diff --name-only --diff-filter=d "${base}" "${CI_COMMIT_SHA}" 2>/dev/null || true
}

FILES=()
while IFS= read -r line || [ -n "${line:-}" ]; do
  [ -n "${line:-}" ] && FILES+=("$line")
done < <(collect_changed | sort -u | sed '/^$/d')

if [ "${#FILES[@]}" -eq 0 ]; then
  echo "pre-commit: 无变更文件（diff 范围为空），跳过。"
  exit 0
fi

echo "pre-commit: 检查 ${#FILES[@]} 个变更文件"
exec pre-commit run --files "${FILES[@]}"
