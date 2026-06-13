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
#   # 1) 跑内置默认清单（首次批量迁移）
#   bash scripts/migrate_personal_images.sh
#
#   # 2) SOP：别人给一个镜像，临时转一个（推荐日常用法）
#   #    只给源 -> 目标默认 dptech/matmaster/<原名:原tag>
#   bash scripts/migrate_personal_images.sh registry.dp.tech/dptech/dp/native/prod-20000/foo:bar
#   #    给源 + 目标 -> 可顺手改名（源/目标都可省略 registry.dp.tech/ 前缀）
#   bash scripts/migrate_personal_images.sh prod-20000/a1:bar dptech/matmaster/gromacs:bar
#
#   # 任意用法前加 DRY_RUN=1 只打印将执行的命令（凭证已脱敏）
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
: "${REGISTRY_USERNAME:?请在 .env 中设置 REGISTRY_USERNAME}"
: "${REGISTRY_PASSWORD:?请在 .env 中设置 REGISTRY_PASSWORD}"

if [ "$DRY_RUN" != "1" ] && ! command -v skopeo >/dev/null 2>&1; then
    echo "未找到 skopeo，请先安装：brew install skopeo 或 apt-get install -y skopeo" >&2
    exit 1
fi

# 内置默认清单，格式：源相对路径|目标相对路径（均相对 ${REGISTRY}，保留各自 tag）
# 目标统一收敛到 dptech/matmaster，并规范化名字
DEFAULT_MIGRATIONS=(
    "dptech/dp/native/prod-19853/orca:v6.1.1|dptech/matmaster/orca:v6.1.1"
    "dptech/dp/native/prod-19853/pyscf-geometric:dev-260608|dptech/matmaster/pyscf-geometric:dev-260608"
    "dptech/dp/native/prod-19853/mlips:dev-0421|dptech/matmaster/mlips:dev-0421"
    "dptech/dp/native/prod-19853/abinit:v9.10.3_pp|dptech/matmaster/abinit:v9.10.3_pp"
    "dptech/dp/native/prod-19853/xrd-app:dev-260119|dptech/matmaster/xrd-app:dev-260119"
    "dptech/dp/native/hub/mrdic2/abacusp:1.0.3-1778742780|dptech/matmaster/abacus:1.0.3-1778742780"
    "dptech/dp/native/hub/mrdic2/a1:1.0.1-1779698340|dptech/matmaster/gromacs:1.0.1-1779698340"
    "dptech/dp/native/hub/mrdic2/gpumd:1.0.2-1777991160|dptech/matmaster/gpumd:1.0.2-1777991160"
)

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
        # 目标默认 dptech/matmaster/<源镜像最后一段 name:tag>
        dst_rel="dptech/matmaster/${src_rel##*/}"
    fi
    MIGRATIONS=("${src_rel}|${dst_rel}")
else
    MIGRATIONS=("${DEFAULT_MIGRATIONS[@]}")
fi

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

echo "全部完成。确认无误后可手动删除对应源镜像，删除不影响 matmaster 下镜像。"
