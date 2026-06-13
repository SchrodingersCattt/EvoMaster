#!/usr/bin/env bash
# 检查 SKILL.md 中是否引用了「个人名下」镜像。
#
# 判定规则：registry.dp.tech 下命名空间形如 prod-<编号>（例如
#   registry.dp.tech/dptech/dp/native/prod-19853/orca:v6.1.1）即视为个人名下镜像，
#   命中即失败，须改用共享命名空间镜像后再合入。
#
# 可独立本地运行：bash ci/check_personal_images.sh
set -euo pipefail

ROOT="${CI_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$ROOT"

# registry.dp.tech/<...>/prod-<数字> ：prod- 紧跟在路径分隔符之后，避免误伤镜像名
PATTERN='registry\.dp\.tech/[^[:space:]]*/prod-[0-9]+'

hits="$(grep -REn --include='SKILL.md' -- "$PATTERN" . || true)"

if [ -n "$hits" ]; then
    echo "✗ 检测到 SKILL.md 引用了个人名下镜像（prod-<编号> 命名空间），CI 失败：" >&2
    echo "$hits" >&2
    echo >&2
    echo "请改用共享命名空间镜像（如 registry.dp.tech/dptech/<name>:<tag>）后重试。" >&2
    exit 1
fi

echo "✓ 未发现个人名下镜像引用（SKILL.md）。"
