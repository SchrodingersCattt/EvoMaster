#!/usr/bin/env bash
# 将个人名下镜像（registry.dp.tech 下 prod-<编号> 或 hub/<用户名> 命名空间）
# 重新打 tag，迁移到共享命名空间 registry.dp.tech/dptech/matmaster 下。
# 顺手规范化镜像名（如 a1->gromacs、abacusp->abacus）。
#
# - 凭证从项目根 .env 读取：REGISTRY_USERNAME / REGISTRY_PASSWORD（.env 已在 .gitignore）。
# - 依赖 skopeo：brew install skopeo  或  apt-get install -y skopeo。
# - registry 间直传，不落地本地磁盘；--all 保留多架构 manifest。
# - 仅「复制 + 校验 digest」，不删除源镜像（确认无误后再手动删旧的）。
#
# 用法：
#   bash scripts/migrate_personal_images.sh            # 复制并校验
#   DRY_RUN=1 bash scripts/migrate_personal_images.sh  # 只打印将执行的命令
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
: "${REGISTRY_USERNAME:?请在 .env 中设置 REGISTRY_USERNAME}"
: "${REGISTRY_PASSWORD:?请在 .env 中设置 REGISTRY_PASSWORD}"

if [ "$DRY_RUN" != "1" ] && ! command -v skopeo >/dev/null 2>&1; then
    echo "未找到 skopeo，请先安装：brew install skopeo 或 apt-get install -y skopeo" >&2
    exit 1
fi

# 待迁移镜像，格式：源相对路径|目标相对路径（均相对 ${REGISTRY}，保留各自 tag）
# 目标统一收敛到 dptech/matmaster，并规范化名字
MIGRATIONS=(
    "dptech/dp/native/prod-19853/orca:v6.1.1|dptech/matmaster/orca:v6.1.1"
    "dptech/dp/native/prod-19853/pyscf-geometric:dev-260608|dptech/matmaster/pyscf-geometric:dev-260608"
    "dptech/dp/native/prod-19853/mlips:dev-0421|dptech/matmaster/mlips:dev-0421"
    "dptech/dp/native/prod-19853/abinit:v9.10.3_pp|dptech/matmaster/abinit:v9.10.3_pp"
    "dptech/dp/native/prod-19853/xrd-app:dev-260119|dptech/matmaster/xrd-app:dev-260119"
    "dptech/dp/native/hub/mrdic2/abacusp:1.0.3-1778742780|dptech/matmaster/abacus:1.0.3-1778742780"
    "dptech/dp/native/hub/mrdic2/a1:1.0.1-1779698340|dptech/matmaster/gromacs:1.0.1-1779698340"
    "dptech/dp/native/hub/mrdic2/gpumd:1.0.2-1777991160|dptech/matmaster/gpumd:1.0.2-1777991160"
)

CREDS="${REGISTRY_USERNAME}:${REGISTRY_PASSWORD}"

run() {
    if [ "$DRY_RUN" = "1" ]; then
        # 打印时对凭证脱敏，避免明文密码进入日志/终端
        local shown=()
        local a
        for a in "$@"; do
            if [ "$a" = "$CREDS" ]; then
                shown+=("<REGISTRY_USERNAME:REGISTRY_PASSWORD>")
            else
                shown+=("$a")
            fi
        done
        printf 'DRY_RUN >>> %s\n' "${shown[*]}"
    else
        "$@"
    fi
}

fail=0
for pair in "${MIGRATIONS[@]}"; do
    src="${REGISTRY}/${pair%%|*}"
    dst="${REGISTRY}/${pair##*|}"
    echo "==> 复制 ${pair%%|*}  ->  ${pair##*|}"
    run skopeo copy --all \
        --src-creds "$CREDS" \
        --dest-creds "$CREDS" \
        "docker://${src}" "docker://${dst}"

    [ "$DRY_RUN" = "1" ] && continue

    echo "    校验 digest ..."
    src_digest="$(skopeo inspect --creds "$CREDS" --format '{{.Digest}}' "docker://${src}")"
    dst_digest="$(skopeo inspect --creds "$CREDS" --format '{{.Digest}}' "docker://${dst}")"
    if [ "$src_digest" = "$dst_digest" ]; then
        echo "    OK  ${dst}  (${dst_digest})"
    else
        echo "    ✗ digest 不一致：src=${src_digest} dst=${dst_digest}" >&2
        fail=1
    fi
done

if [ "$fail" -ne 0 ]; then
    echo "部分镜像校验失败，请检查后重试（源镜像未删除）。" >&2
    exit 1
fi

echo "全部完成。确认无误后可手动删除源镜像（prod-19853 / hub/mrdic2 命名空间），删除不影响 matmaster 下镜像。"
