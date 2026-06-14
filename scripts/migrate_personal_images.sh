#!/usr/bin/env bash
# 将个人名下镜像（registry.dp.tech 下 prod-<编号> 或 hub/<用户名> 命名空间）
# 重新打 tag，迁移到共享命名空间 registry.dp.tech/dptech/matmaster 下。
# 顺手规范化镜像名（如 a1->gromacs、abacusp->abacus）。
#
# 用 docker buildx imagetools：源/目标在同一 registry 时走 registry 端 cross-repo
# blob mount，数据不经本机，速度极快；需要 docker daemon。
#
# - 凭证从项目根 .env 读取：REGISTRY_USERNAME / REGISTRY_PASSWORD（.env 已在 .gitignore）。
# - 幂等：目标 tag 已存在则跳过（Harbor 多为 immutable tag，不可覆盖）。
# - 仅「复制 + 校验」，不删除源镜像（确认无误后再手动删旧的）。
#
# 用法：
#   # 1) 跑内置默认清单（首次批量迁移）
#   bash scripts/migrate_personal_images.sh
#
#   # 2) SOP：别人给一个镜像，临时转一个（推荐日常用法）
#   #    只给源 -> 目标默认 dptech/matmaster:<原名>-<原tag>
#   bash scripts/migrate_personal_images.sh registry.dp.tech/dptech/dp/native/prod-20000/foo:bar
#   #    给源 + 目标 -> 可顺手改名（源/目标都可省略 registry.dp.tech/ 前缀）
#   bash scripts/migrate_personal_images.sh prod-20000/a1:bar dptech/matmaster:gromacs-bar
#
#   # 只看将执行的命令（不真正推送）
#   DRY_RUN=1 bash scripts/migrate_personal_images.sh ...
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/.." && pwd))"
DRY_RUN="${DRY_RUN:-0}"

# 加载 .env（仅本地用；.env 已 gitignore）
if [ -f "$ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$ROOT/.env"
    set +a
fi

: "${REGISTRY:=registry.dp.tech}"

# 非 DRY_RUN 才需要真实凭证与 docker，并登录到临时配置目录，
# 凭证不经命令行参数传递，避免明文密码出现在进程列表（ps/proc）。
if [ "$DRY_RUN" != "1" ]; then
    : "${REGISTRY_USERNAME:?请在 .env 中设置 REGISTRY_USERNAME}"
    : "${REGISTRY_PASSWORD:?请在 .env 中设置 REGISTRY_PASSWORD}"
    command -v docker >/dev/null 2>&1 || {
        echo "未找到 docker（本脚本依赖 docker buildx）" >&2
        exit 1
    }
    DOCKER_CONFIG="$(mktemp -d)"
    export DOCKER_CONFIG
    trap 'rm -rf "$DOCKER_CONFIG"' EXIT
    printf '%s' "$REGISTRY_PASSWORD" |
        docker login -u "$REGISTRY_USERNAME" --password-stdin "$REGISTRY" >/dev/null
fi

# 内置默认清单从共享映射文件读取（与 SKILL.md 同源，避免两处维护）
# 文件格式：源相对路径|目标相对路径（均相对 ${REGISTRY}），# 开头为注释
MIGRATIONS_FILE="${ROOT}/ci/personal_image_migrations.tsv"
DEFAULT_MIGRATIONS=()
if [ -f "$MIGRATIONS_FILE" ]; then
    while IFS= read -r line; do
        case "$line" in '' | \#*) continue ;; esac
        DEFAULT_MIGRATIONS+=("$line")
    done <"$MIGRATIONS_FILE"
fi

# 去掉 docker:// 与 registry 前缀，返回相对路径
normalize_ref() {
    local x="$1"
    x="${x#docker://}"
    x="${x#"${REGISTRY}"/}"
    printf '%s' "$x"
}

# 有命令行参数：转单个（SOP 日常用法）；否则跑内置默认清单
if [ "$#" -ge 1 ]; then
    src_rel="$(normalize_ref "$1")"
    if [ "$#" -ge 2 ]; then
        dst_rel="$(normalize_ref "$2")"
    else
        # 目标默认 dptech/matmaster:<原名>-<原tag>（单 repository + tag，name:tag 的冒号转连字符）
        last="${src_rel##*/}"
        dst_rel="dptech/matmaster:${last/:/-}"
    fi
    MIGRATIONS=("${src_rel}|${dst_rel}")
else
    MIGRATIONS=("${DEFAULT_MIGRATIONS[@]}")
fi

run() {
    if [ "$DRY_RUN" = "1" ]; then
        printf 'DRY_RUN >>> %s\n' "$*"
    else
        "$@"
    fi
}

# 目标是否已存在
dst_exists() {
    docker buildx imagetools inspect "$1" >/dev/null 2>&1
}

fail=0
for pair in "${MIGRATIONS[@]}"; do
    src="${REGISTRY}/${pair%%|*}"
    dst="${REGISTRY}/${pair##*|}"
    echo "==> 复制 ${pair%%|*}  ->  ${pair##*|}"
    # 幂等：目标 tag 已存在则跳过（Harbor 多为 immutable tag，不可覆盖）
    if [ "$DRY_RUN" != "1" ] && dst_exists "$dst"; then
        echo "    目标已存在，跳过：${dst}"
        continue
    fi
    run docker buildx imagetools create -t "$dst" "$src"

    [ "$DRY_RUN" = "1" ] && continue

    # 校验：imagetools create 引用源 manifest digest，成功即一致，确认目标存在即可
    if dst_exists "$dst"; then
        echo "    OK  ${dst}"
    else
        echo "    ✗ 目标创建后不可见：${dst}" >&2
        fail=1
    fi
done

if [ "$fail" -ne 0 ]; then
    echo "部分镜像迁移失败，请检查后重试（源镜像未删除）。" >&2
    exit 1
fi

echo "全部完成。确认无误后可手动删除对应源镜像，删除不影响 matmaster 下镜像。"
