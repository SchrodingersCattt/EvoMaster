#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEFAULT_REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="${REPO_ROOT:-${DEFAULT_REPO_ROOT}}"

SOURCE_HOOK="${REPO_ROOT}/.pre-commit/pre-push"
TARGET_HOOK="${REPO_ROOT}/.git/hooks/pre-push"

if [ ! -f "${SOURCE_HOOK}" ]; then
  echo "找不到仓库内 hook 模板: ${SOURCE_HOOK}" >&2
  exit 1
fi

if [ ! -d "${REPO_ROOT}/.git/hooks" ]; then
  echo "找不到 git hooks 目录: ${REPO_ROOT}/.git/hooks" >&2
  exit 1
fi

cp "${SOURCE_HOOK}" "${TARGET_HOOK}"
chmod +x "${TARGET_HOOK}"

echo "已安装 pre-push hook: ${TARGET_HOOK}"
