#!/usr/bin/env bash
# 将个人名下镜像（registry.dp.tech 下 prod-<编号> 或 hub/<用户名> 命名空间）
# 重新打 tag，迁移到共享命名空间 registry.dp.tech/dptech/matmaster 下。
# 顺手规范化镜像名（如 a1->gromacs、abacusp->abacus）。
#
# 两种后端（环境变量 BACKEND 选择）：
#   - buildx（默认）：docker buildx imagetools create。源/目标在同一 registry 时，
#     走 registry 端 cross-repo blob mount，数据不经本机，速度极快；需要 docker daemon。
#   - skopeo：registry 间 client 中转复制（会经本机下行+上行）；跨 registry 场景的 fallback。
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
#   # 切换后端 / 只看将执行的命令（凭证已脱敏）
#   BACKEND=skopeo bash scripts/migrate_personal_images.sh
#   DRY_RUN=1 bash scripts/migrate_personal_images.sh ...
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/.." && pwd))"
DRY_RUN="${DRY_RUN:-0}"
BACKEND="${BACKEND:-buildx}"

case "$BACKEND" in
    buildx | skopeo) ;;
    *)
        echo "未知 BACKEND=$BACKEND（可选 buildx | skopeo）" >&2
        exit 1
        ;;
esac

# 加载 .env（仅本地用；.env 已 gitignore）
if [ -f "$ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$ROOT/.env"
    set +a
fi

: "${REGISTRY:=registry.dp.tech}"

# 非 DRY_RUN 才需要真实凭证与对应工具，并提前登录到临时配置目录，
# 凭证不经命令行参数传递，避免明文密码出现在进程列表（ps/proc）。
if [ "$DRY_RUN" != "1" ]; then
    : "${REGISTRY_USERNAME:?请在 .env 中设置 REGISTRY_USERNAME}"
    : "${REGISTRY_PASSWORD:?请在 .env 中设置 REGISTRY_PASSWORD}"
    if [ "$BACKEND" = "buildx" ]; then
        command -v docker >/dev/null 2>&1 || {
            echo "未找到 docker（BACKEND=buildx 需要）；或改用 BACKEND=skopeo" >&2
            exit 1
        }
        DOCKER_CONFIG="$(mktemp -d)"
        export DOCKER_CONFIG
        trap 'rm -rf "$DOCKER_CONFIG"' EXIT
        printf '%s' "$REGISTRY_PASSWORD" |
            docker login -u "$REGISTRY_USERNAME" --password-stdin "$REGISTRY" >/dev/null
    else
        command -v skopeo >/dev/null 2>&1 || {
            echo "未找到 skopeo，请先安装：brew install skopeo 或 apt-get install -y skopeo" >&2
            exit 1
        }
        REGISTRY_AUTH_FILE="$(mktemp)"
        export REGISTRY_AUTH_FILE
        trap 'rm -f "$REGISTRY_AUTH_FILE"' EXIT
        # mktemp 产生空文件，skopeo login 会先读取该 auth 文件，需初始化为合法空 JSON
        printf '{}' >"$REGISTRY_AUTH_FILE"
        printf '%s' "$REGISTRY_PASSWORD" |
            skopeo login -u "$REGISTRY_USERNAME" --password-stdin "$REGISTRY"
    fi
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

# 跨平台 sha256（Linux: sha256sum；macOS: shasum -a 256），读 stdin 输出纯哈希
sha256_stdin() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum | awk '{print $1}'
    else
        shasum -a 256 | awk '{print $1}'
    fi
}

# 后端：目标是否已存在（$1=目标完整 ref，不带 docker://）
dst_exists() {
    if [ "$BACKEND" = "buildx" ]; then
        docker buildx imagetools inspect "$1" >/dev/null 2>&1
    else
        skopeo inspect --raw "docker://$1" >/dev/null 2>&1
    fi
}

# 后端：复制（$1=源，$2=目标，均为完整 ref，不带 docker://）
do_copy() {
    if [ "$BACKEND" = "buildx" ]; then
        run docker buildx imagetools create -t "$2" "$1"
    else
        run skopeo copy --all "docker://$1" "docker://$2"
    fi
}

# 后端：校验（$1=源，$2=目标）。buildx 的 create 引用源 manifest digest，成功即一致，
# 确认目标存在即可；skopeo 取 --raw manifest 自算 sha256 比对（兼容多架构 list）。
verify_copy() {
    local src="$1" dst="$2"
    if [ "$BACKEND" = "buildx" ]; then
        if dst_exists "$dst"; then
            echo "    OK  ${dst}"
        else
            echo "    ✗ 目标创建后不可见：${dst}" >&2
            fail=1
        fi
        return
    fi
    local src_raw dst_raw src_digest dst_digest
    src_raw="$(skopeo inspect --raw "docker://${src}" 2>/dev/null || true)"
    dst_raw="$(skopeo inspect --raw "docker://${dst}" 2>/dev/null || true)"
    if [ -z "$src_raw" ] || [ -z "$dst_raw" ]; then
        echo "    ✗ 获取 manifest 失败（src 或 dst inspect 无输出）" >&2
        fail=1
        return
    fi
    src_digest="$(printf '%s' "$src_raw" | sha256_stdin)"
    dst_digest="$(printf '%s' "$dst_raw" | sha256_stdin)"
    if [ "$src_digest" = "$dst_digest" ]; then
        echo "    OK  ${dst}  (sha256:${dst_digest})"
    else
        echo "    ✗ digest 不一致：src=${src_digest} dst=${dst_digest}" >&2
        fail=1
    fi
}

echo "后端：${BACKEND}"
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
    do_copy "$src" "$dst"

    [ "$DRY_RUN" = "1" ] && continue

    echo "    校验 ..."
    verify_copy "$src" "$dst"
done

if [ "$fail" -ne 0 ]; then
    echo "部分镜像迁移/校验失败，请检查后重试（源镜像未删除）。" >&2
    exit 1
fi

echo "全部完成。确认无误后可手动删除对应源镜像，删除不影响 matmaster 下镜像。"
