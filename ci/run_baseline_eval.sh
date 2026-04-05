#!/usr/bin/env bash
# CI 内部执行脚本：支持 Claude CLI baseline 和 MatMaster DevShell 两种评测模式
# 在项目 Docker 容器内运行，由动态生成的 ci/generated-eval-child.yml 中的 job 调用。
#
# 模式选择（EVAL_RUNNER）：
#   claude_cli（默认） — prepare → claude -p 跑题 → 默认每题 finalize +（pending 时）评分并 POST；或整轮 finalize + STEP3
#   devshell           — run_devshell_eval.py 一步完成（MatMaster Agent 内部跑题）→（默认）阶段二同上
#
# 环境变量（须在 GitLab CI Variables 中配置）：
#   通用:
#     EVAL_RUNNER                    — claude_cli 或 devshell，默认 claude_cli
#     MATMASTER_TOOLS_SERVER         — ingest 入库地址（必须）
#     MATMASTER_TOOLS_EVALUATION_BEARER — ingest Bearer token（必须）
#     OSS_ENDPOINT / OSS_BUCKET_NAME / OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET
#                                    — 可选；finalize 上传 task 产物 zip 到 OSS（须由 GitLab 子 job 的 docker create -e 透传进容器）
#     BASELINE_CAPABILITIES          — 逗号分隔 capability，默认 structure_construction
#     BASELINE_MODES                 — direct/planner/direct planner，默认 direct
#     BASELINE_LIMIT                 — 仅 capabilities 布局：每类最多几题（0=不限），默认 0
#     BASELINE_MODEL                 — 模型标识（空=使用默认）
#     BASELINE_RUN_LABEL             — run 目录前缀，默认 baseline_cc
#     BASELINE_PENDING_ONLY          — 1=pending模式（人工阅卷），0=proxy自动入库，默认 1
#   子流水线布局（由生成器 / docker -e 注入）:
#     BASELINE_EVAL_LAYOUT           — capabilities（默认）或 questions
#     BASELINE_QUESTIONS             — 逗号分隔 question id；覆盖 ci/baseline_eval_preset.yaml 的 question_ids
#   题库与布局预设（仓库内文件，见 ci/baseline_eval_preset.yaml）:
#     child_pipeline                 — capabilities | questions（可被 CI 变量 BASELINE_CHILD_PIPELINE 覆盖）
#     questions_mode                 — preset | score_summary_missing_cc（仅 yaml，见 ci/baseline_eval_preset.yaml）
#     question_ids                   — questions 布局下的题目列表（BASELINE_LIMIT 不用于封顶缺分列表；
#                                      capabilities 布局下仍用 BASELINE_LIMIT 限制每类题数）
#   Claude CLI 模式专用:
#     ANTHROPIC_API_KEY 或 ANTHROPIC_AUTH_TOKEN — Claude CLI 鉴权（二选一）
#     ANTHROPIC_BASE_URL             — Claude CLI 端点（如 MiniMax/gpugeek 兼容端点）
#     ANTHROPIC_MODEL                — Claude CLI 指定模型名
#   Claude Code + AWS Bedrock（任选其一触发：CLAUDE_CODE_USE_BEDROCK=1 / ANTHROPIC_PLATFORM=bedrock / BASELINE_CLAUDE_BEDROCK=1）:
#     须先有 AWS 凭证（见上方 AWS CLI）；Bedrock **不使用** ANTHROPIC_MODEL（那是 gpugeek/兼容 API 路由名），而用:
#     ANTHROPIC_BEDROCK_MODEL（默认 us.anthropic.claude-opus-4-6-v1[1m]）
#     ANTHROPIC_BEDROCK_SMALL_FAST_MODEL（默认 us.anthropic.claude-haiku-4-5-20251001-v1:0）
#     脚本会 export 为 ANTHROPIC_MODEL / ANTHROPIC_SMALL_FAST_MODEL 供 Claude Code 读取
#     ANTHROPIC_PLATFORM=bedrock、AWS_PROFILE=default、CLAUDE_CODE_USE_BEDROCK=1、CLAUDE_CODE_EFFORT_LEVEL（默认 max）
#     Bedrock 模式下会在 aws configure 后执行 list-foundation-models 探测（失败则 exit 1，无法评测）
#     BASELINE_MAX_TURNS             — 每题最大对话轮数，默认 50
#     BASELINE_TIMEOUT               — 每题超时秒数，默认 900
#     BASELINE_CLAUDE_JOBS           — 同时跑几道 claude -p（run_claude_cli_baseline_tasks.py --jobs），默认 4
#     BASELINE_CLAUDE_PER_TASK_PIPELINE — 1（默认）每题结束后 finalize（--only-tasks 合并 raw_runs）+ pending 时逐题 score+ingest；0=整轮结束再 finalize，阶段二仍走 STEP3
#   DevShell 模式专用:
#     LITELLM_PROXY_API_KEY          — LiteLLM 鉴权（llm_config.yaml 中 ${LITELLM_PROXY_API_KEY}）
#     LITELLM_PROXY_API_BASE         — LiteLLM base_url
#   阶段二（BinaryEvaluator 评分 + ingest POST；claude_cli 且 BASELINE_CLAUDE_PER_TASK_PIPELINE=1 时在 STEP2 逐题完成，跳过本节）:
#     BASELINE_SCORE_SUBMIT          — 1（默认）执行 score_baseline_tasks.py --submit；0 跳过
#     BASELINE_SCORE_EVAL_CONFIG     — 可选，覆盖 evaluation/config.yaml 路径（容器内绝对路径或相对 /app）
#     BASELINE_SCORE_EVAL_INGEST_TIMEOUT — 可选，每题 ingest HTTP 超时秒数，默认 120
#   questions 布局 + 自动选题（缺 Claude Code 基线分，来自 tools-server 大表）:
#     启用方式: ci/baseline_eval_preset.yaml 中 questions_mode: score_summary_missing_cc
#     行为: GET .../evaluation/questions/score-summary，跑齐 claude_code_score 为 null 的题目
#       （与 preset 中 question_ids / BASELINE_QUESTIONS 交集）；交集为空则 exit 0。须 MATMASTER_TOOLS_*。
#       缺分列表不按 BASELINE_LIMIT 截断（BASELINE_LIMIT 仅用于 capabilities 布局）。
#     BASELINE_SCORE_SUMMARY_TIMEOUT — 可选，score-summary GET 超时秒数，默认 120
#   SOCKS5 出网（可选，镜像内已带 sthp；不设 BASELINE_STHP_SOCKS 则不启用）:
#     BASELINE_STHP_SOCKS            — SOCKS5 地址 host:port（勿加 socks://；DNS 在 SOCKS 侧解析）
#     BASELINE_STHP_PORT             — sthp 本地 HTTP 监听端口，默认 18080
#     BASELINE_STHP_SOCKS_USER       — SOCKS5 用户名（可选）
#     BASELINE_STHP_SOCKS_PASSWORD   — SOCKS5 密码（可选）
#     BASELINE_SOCKS_PROBE_URL       — SOCKS 直连接探针 URL，默认 http://ifconfig.me（须 curl；失败则 exit 1）
#     BASELINE_SOCKS_PROBE_TIMEOUT_S — 探针超时秒数，默认 30
#     BASELINE_NO_PROXY              — 逗号分隔 NO_PROXY（可选，同时设置 NO_PROXY/no_proxy）
#   AWS CLI（可选，镜像已装 aws v2；须 GitLab Masked 变量）:
#     AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY — 非空时执行 aws configure set（等价交互式 configure）
#     AWS_DEFAULT_REGION               — 默认 us-east-1
#     AWS_DEFAULT_OUTPUT               — 默认 json

set -euo pipefail

APP_DIR="/app"
cd "${APP_DIR}"

# 以脚本路径执行 evaluation/ci 下 .py 时，须能从仓库根解析 evaluation、evomaster 等包。
export PYTHONPATH="${APP_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

if [[ -x "${APP_DIR}/.venv/bin/python" ]]; then
    PY="${APP_DIR}/.venv/bin/python"
else
    PY="python3"
fi

STHP_PID=""
_baseline_stop_sthp() {
    if [[ -n "${STHP_PID}" ]] && kill -0 "${STHP_PID}" 2>/dev/null; then
        kill "${STHP_PID}" 2>/dev/null || true
    fi
}
trap _baseline_stop_sthp EXIT

# 在设置 HTTP_PROXY / 启动 sthp 之前：直连 SOCKS 测出口（与 sthp -s 同源变量）
_baseline_probe_socks_curl() {
    local socks="${BASELINE_STHP_SOCKS:-}"
    [[ -z "${socks}" ]] && return 0
    if ! command -v curl &>/dev/null; then
        echo "[ERROR] BASELINE_STHP_SOCKS 已设置但未找到 curl，无法做 SOCKS 探针" >&2
        exit 1
    fi
    local url="${BASELINE_SOCKS_PROBE_URL:-http://ifconfig.me}"
    local tmo="${BASELINE_SOCKS_PROBE_TIMEOUT_S:-30}"
    local socks_arg="${socks}"
    if [[ -n "${BASELINE_STHP_SOCKS_USER:-}" ]]; then
        socks_arg="${BASELINE_STHP_SOCKS_USER}:${BASELINE_STHP_SOCKS_PASSWORD:-}@${socks}"
    fi
    if [[ -n "${BASELINE_STHP_SOCKS_USER:-}" ]]; then
        echo "[CI] SOCKS 探针: curl --socks5 user:***@${socks} ${url} (timeout ${tmo}s)" >&2
    else
        echo "[CI] SOCKS 探针: curl --socks5 ${socks} ${url} (timeout ${tmo}s)" >&2
    fi
    set +e
    local out rc
    out=$(curl -fsS --max-time "${tmo}" --socks5 "${socks_arg}" "${url}" 2>&1)
    rc=$?
    set -e
    if [[ "${rc}" -eq 0 ]]; then
        echo "[CI] SOCKS 探针成功: ${out}" >&2
    else
        echo "[ERROR] SOCKS 探针失败（exit ${rc}），代理不可用，中止评测: ${out}" >&2
        exit 1
    fi
}

_baseline_probe_socks_curl

_baseline_maybe_start_sthp() {
    local socks="${BASELINE_STHP_SOCKS:-}"
    [[ -z "${socks}" ]] && return 0
    local bin=""
    if command -v sthp >/dev/null 2>&1; then
        bin="$(command -v sthp)"
    elif [[ -x /usr/local/bin/sthp ]]; then
        bin="/usr/local/bin/sthp"
    else
        echo "[ERROR] BASELINE_STHP_SOCKS 已设置但未找到 sthp（需 Dockerfile.eval 构建进镜像）" >&2
        exit 1
    fi
    local port="${BASELINE_STHP_PORT:-18080}"
    local -a args=(-p "${port}" -s "${socks}")
    [[ -n "${BASELINE_STHP_SOCKS_USER:-}" ]] && args+=(-u "${BASELINE_STHP_SOCKS_USER}")
    [[ -n "${BASELINE_STHP_SOCKS_PASSWORD:-}" ]] && args+=(-P "${BASELINE_STHP_SOCKS_PASSWORD}")
    echo "[CI] 启动 sthp：HTTP 127.0.0.1:${port} -> SOCKS5 ${socks}" >&2
    "${bin}" "${args[@]}" &
    STHP_PID=$!
    sleep 0.5
    if ! kill -0 "${STHP_PID}" 2>/dev/null; then
        echo "[ERROR] sthp 进程已退出" >&2
        exit 1
    fi
    export HTTP_PROXY="http://127.0.0.1:${port}"
    export HTTPS_PROXY="http://127.0.0.1:${port}"
    if [[ -n "${BASELINE_NO_PROXY:-}" ]]; then
        export NO_PROXY="${BASELINE_NO_PROXY}"
        export no_proxy="${BASELINE_NO_PROXY}"
    fi
}

_baseline_maybe_start_sthp

_baseline_maybe_configure_aws() {
    if [[ -z "${AWS_ACCESS_KEY_ID:-}" ]] || [[ -z "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
        return 0
    fi
    if ! command -v aws >/dev/null 2>&1; then
        echo "[WARN] 已设置 AWS_ACCESS_KEY_ID/SECRET 但未找到 aws 命令" >&2
        return 0
    fi
    export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
    export AWS_DEFAULT_OUTPUT="${AWS_DEFAULT_OUTPUT:-json}"
    mkdir -p "${HOME}/.aws"
    aws configure set aws_access_key_id "${AWS_ACCESS_KEY_ID}"
    aws configure set aws_secret_access_key "${AWS_SECRET_ACCESS_KEY}"
    aws configure set default.region "${AWS_DEFAULT_REGION}"
    aws configure set default.output "${AWS_DEFAULT_OUTPUT}"
    echo "[CI] 已写入 ~/.aws（region=${AWS_DEFAULT_REGION} output=${AWS_DEFAULT_OUTPUT}）" >&2
}

_baseline_maybe_configure_aws

# 与 settings.json 分支共用：任一成立即视为走 Bedrock（须避免再写 ANTHROPIC_BASE_URL / 第三方 API_KEY）
_baseline_claude_bedrock_enabled() {
    [[ "${CLAUDE_CODE_USE_BEDROCK:-}" == "1" ]] \
        || [[ "${ANTHROPIC_PLATFORM:-}" == "bedrock" ]] \
        || [[ "${BASELINE_CLAUDE_BEDROCK:-}" == "1" ]]
}

# Claude Code 走 Bedrock 时由 CI 注入 CLAUDE_CODE_USE_BEDROCK=1 等；此处补齐默认模型与 effort
_baseline_maybe_export_claude_bedrock_env() {
    if ! _baseline_claude_bedrock_enabled; then
        return 0
    fi
    export ANTHROPIC_PLATFORM="${ANTHROPIC_PLATFORM:-bedrock}"
    export AWS_PROFILE="${AWS_PROFILE:-default}"
    export CLAUDE_CODE_USE_BEDROCK=1
    export ANTHROPIC_MODEL="${ANTHROPIC_BEDROCK_MODEL:-us.anthropic.claude-opus-4-6-v1[1m]}"
    export ANTHROPIC_SMALL_FAST_MODEL="${ANTHROPIC_BEDROCK_SMALL_FAST_MODEL:-us.anthropic.claude-haiku-4-5-20251001-v1:0}"
    export CLAUDE_CODE_EFFORT_LEVEL="${CLAUDE_CODE_EFFORT_LEVEL:-max}"
    echo "[CI] Claude Code：Bedrock（AWS_PROFILE=${AWS_PROFILE} ANTHROPIC_MODEL=${ANTHROPIC_MODEL}）" >&2
}

_baseline_maybe_export_claude_bedrock_env

# Bedrock 连通性：与 Claude 相同走 HTTP(S)_PROXY（sthp→SOCKS）；需 IAM bedrock:ListFoundationModels
_baseline_probe_bedrock_list_models() {
    if ! _baseline_claude_bedrock_enabled; then
        return 0
    fi
    if ! command -v aws &>/dev/null; then
        echo "[ERROR] Bedrock 模式已启用但未找到 aws CLI，无法 list-foundation-models" >&2
        exit 1
    fi
    local br_region="${AWS_DEFAULT_REGION:-us-east-1}"
    local _tmp
    _tmp="$(mktemp)"
    echo "[CI] Bedrock 连通性探测: aws bedrock list-foundation-models --region ${br_region}" >&2
    set +e
    aws bedrock list-foundation-models --region "${br_region}" --output json --no-cli-pager >"${_tmp}" 2>&1
    local rc=$?
    set -e
    if [[ "${rc}" -eq 0 ]]; then
        head -c 8000 "${_tmp}"
        echo ""
        echo "[CI] list-foundation-models 成功（exit 0；上文为响应前 8KB）" >&2
    else
        head -c 4000 "${_tmp}" >&2
        echo "" >&2
        echo "[ERROR] list-foundation-models 失败（exit ${rc}），Bedrock 不可用，中止评测（SOCKS/代理、网络或 IAM: bedrock:ListFoundationModels）。" >&2
        rm -f "${_tmp}"
        exit 1
    fi
    rm -f "${_tmp}"
}

_baseline_probe_bedrock_list_models

_baseline_write_skip_artifacts() {
    local reason="$1"
    mkdir -p "${APP_DIR}/results"
    # 空目录可能无法作为 GitLab artifact 上传；占位文件便于 docker cp 与子 job 收集。
    touch "${APP_DIR}/results/.baseline_skipped"
    {
        echo "BASELINE_SKIPPED=1"
        echo "BASELINE_SKIP_REASON=${reason}"
        echo "BASELINE_SKIP_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } >"${APP_DIR}/baseline_run.env"
}

# ── 通用参数解析 ─────────────────────────────────────────────────────────────
EVAL_RUNNER="${EVAL_RUNNER:-claude_cli}"
CAPABILITIES="${BASELINE_CAPABILITIES:-structure_construction}"
MODES="${BASELINE_MODES:-direct}"
LIMIT="${BASELINE_LIMIT:-0}"
MODEL="${BASELINE_MODEL:-}"
RUN_LABEL="${BASELINE_RUN_LABEL:-baseline_cc}"
PENDING_ONLY="${BASELINE_PENDING_ONLY:-1}"
LAYOUT="${BASELINE_EVAL_LAYOUT:-capabilities}"

Q_IDS=()
QUESTIONS_MODE="preset"
if [[ "${LAYOUT}" == "questions" ]]; then
    QUESTIONS_MODE="$("${PY}" "${APP_DIR}/ci/baseline_eval_preset.py" questions-mode)"
    if [[ "${QUESTIONS_MODE}" == "score_summary_missing_cc" ]]; then
        echo "[CI] questions_mode=${QUESTIONS_MODE}：从 matmaster-tools-server GET /api/v1/evaluation/questions/score-summary 选取缺 Claude Code 基线分的题目"
        PRE_IDS_FILE="$(mktemp)"
        "${PY}" "${APP_DIR}/ci/baseline_eval_preset.py" list-ids > "${PRE_IDS_FILE}" || true
        FETCH_CMD=(
            "${PY}" "${APP_DIR}/evaluation/scripts/baseline/fetch_missing_baseline_from_score_summary.py"
            "--timeout" "${BASELINE_SCORE_SUMMARY_TIMEOUT:-120}"
        )
        if [[ -s "${PRE_IDS_FILE}" ]]; then
            FETCH_CMD+=(--intersect-file "${PRE_IDS_FILE}")
        fi
        Q_IDS_FILE="$(mktemp)"
        if ! "${FETCH_CMD[@]}" >"${Q_IDS_FILE}"; then
            rm -f "${Q_IDS_FILE}" "${PRE_IDS_FILE}"
            echo "[ERROR] fetch_missing_baseline_from_score_summary 失败（见上方 Python 输出）。" >&2
            exit 1
        fi
        mapfile -t Q_IDS <"${Q_IDS_FILE}"
        rm -f "${Q_IDS_FILE}" "${PRE_IDS_FILE}"
        if [[ ${#Q_IDS[@]} -eq 0 ]]; then
            echo "[INFO] score-summary 与预设交集后无待跑题目（Claude Code 基线均已覆盖），退出 0。"
            _baseline_write_skip_artifacts "no_questions_after_score_summary"
            exit 0
        fi
    else
        Q_LIST_FILE="$(mktemp)"
        if ! "${PY}" "${APP_DIR}/ci/baseline_eval_preset.py" list-ids >"${Q_LIST_FILE}"; then
            rm -f "${Q_LIST_FILE}"
            echo "[ERROR] baseline_eval_preset.py list-ids 失败。" >&2
            exit 1
        fi
        mapfile -t Q_IDS <"${Q_LIST_FILE}"
        rm -f "${Q_LIST_FILE}"
        if [[ ${#Q_IDS[@]} -eq 0 ]]; then
            echo "[ERROR] BASELINE_EVAL_LAYOUT=questions 但未解析到任何 question id（检查 BASELINE_QUESTIONS 或 ci/baseline_eval_preset.yaml）"
            exit 1
        fi
    fi
fi

echo "=== CI Baseline Eval ==="
echo "  runner       : ${EVAL_RUNNER}"
echo "  layout       : ${LAYOUT}"
if [[ "${LAYOUT}" == "questions" ]]; then
    echo "  questions_mode: ${QUESTIONS_MODE}"
    echo "  question_ids : ${Q_IDS[*]}"
else
    echo "  capabilities : ${CAPABILITIES}"
    echo "  limit        : ${LIMIT} (0=无限制)"
fi
echo "  modes        : ${MODES}"
echo "  model        : ${MODEL:-<默认>}"
echo "  run_label    : ${RUN_LABEL}"
echo "  pending_only : ${PENDING_ONLY}"
echo "  score_submit : ${BASELINE_SCORE_SUBMIT:-1} (pending_only=1 且 pending_ingest/*.json 存在时跑阶段二)"

# capability / mode 参数：逗号分隔 → 空格分隔
CAPS_ARGS=$(echo "${CAPABILITIES}" | tr ',' ' ')
MODES_ARGS=$(echo "${MODES}" | tr ',' ' ')

# ── 激活 Python 环境 ─────────────────────────────────────────────────────────
source "${APP_DIR}/.venv/bin/activate"

# ══════════════════════════════════════════════════════════════════════════════
#  DevShell 模式
# ══════════════════════════════════════════════════════════════════════════════
if [[ "${EVAL_RUNNER}" == "devshell" ]]; then
    echo "  (devshell 模式：MatMaster Agent 内部跑题)"
    echo "======================================="

    DEVSHELL_CMD=(
        python evaluation/scripts/devshell/run_devshell_eval.py
        --run-label "${RUN_LABEL}"
        --modes ${MODES_ARGS}
        --no-clean-results
    )
    if [[ "${LAYOUT}" == "questions" ]]; then
        DEVSHELL_CMD+=(--questions "${Q_IDS[@]}")
    else
        DEVSHELL_CMD+=(--capabilities ${CAPS_ARGS})
        if [[ "${LIMIT}" -gt 0 ]]; then
            DEVSHELL_CMD+=(--limit "${LIMIT}")
        fi
    fi
    if [[ -n "${MODEL}" ]]; then
        DEVSHELL_CMD+=(--model "${MODEL}")
    fi
    if [[ "${PENDING_ONLY}" == "1" ]]; then
        DEVSHELL_CMD+=(--eval-ingest-pending-only)
    fi

    echo ""
    echo "[STEP 1] DevShell 跑题（prepare + agent run + ingest 一步完成）..."
    DEVSHELL_EXIT=0
    "${DEVSHELL_CMD[@]}" || DEVSHELL_EXIT=$?

# ══════════════════════════════════════════════════════════════════════════════
#  Claude CLI 模式
# ══════════════════════════════════════════════════════════════════════════════
elif [[ "${EVAL_RUNNER}" == "claude_cli" ]]; then
    MAX_TURNS="${BASELINE_MAX_TURNS:-50}"
    TIMEOUT_S="${BASELINE_TIMEOUT:-900}"
    CLAUDE_JOBS="${BASELINE_CLAUDE_JOBS:-4}"
    CLAUDE_PER_TASK_PIPELINE="${BASELINE_CLAUDE_PER_TASK_PIPELINE:-1}"
    SCORE_INGEST_TIMEOUT="${BASELINE_SCORE_EVAL_INGEST_TIMEOUT:-120}"
    echo "  max_turns    : ${MAX_TURNS}"
    echo "  timeout(s)   : ${TIMEOUT_S}"
    echo "  claude_jobs  : ${CLAUDE_JOBS} (parallel claude -p)"
    echo "  per_task_ff  : ${CLAUDE_PER_TASK_PIPELINE} (1=每题 finalize+评分上报)"
    echo "  (claude_cli 模式：Claude Code CLI 跑题)"
    echo "======================================="

    # ── 确认 claude CLI 可用（由 Dockerfile.eval 预装）────────────────────────
    if ! command -v claude &>/dev/null; then
        echo "[ERROR] claude CLI 不在 PATH。请确认使用 Dockerfile.eval 构建的镜像。"
        exit 1
    fi
    echo "[CI] claude CLI: $(claude --version 2>/dev/null || echo 'unknown')"

    # ── 写入 claude settings.json（注入代理/模型配置）──────────────────────────
    CLAUDE_HOME="${HOME}/.claude"
    mkdir -p "${CLAUDE_HOME}"

    # Bedrock 须优先于「API_KEY + BASE_URL」：否则 CI 里残留的 gpugeek 等会写入 settings，Claude 仍走第三方 HTTP
    if _baseline_claude_bedrock_enabled; then
        cat > "${CLAUDE_HOME}/settings.json" <<EOF
{
  "env": {
    "ANTHROPIC_PLATFORM": "${ANTHROPIC_PLATFORM:-bedrock}",
    "AWS_PROFILE": "${AWS_PROFILE:-default}",
    "CLAUDE_CODE_USE_BEDROCK": "1",
    "ANTHROPIC_MODEL": "${ANTHROPIC_MODEL}",
    "ANTHROPIC_SMALL_FAST_MODEL": "${ANTHROPIC_SMALL_FAST_MODEL}",
    "CLAUDE_CODE_EFFORT_LEVEL": "${CLAUDE_CODE_EFFORT_LEVEL:-max}",
    "API_TIMEOUT_MS": "3000000",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
  }
}
EOF
        echo "[CI] settings.json 已写入（AWS Bedrock；已忽略 ANTHROPIC_BASE_URL / 第三方 API_KEY）"
        unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL || true
    elif [[ -n "${ANTHROPIC_BASE_URL:-}" && -n "${ANTHROPIC_AUTH_TOKEN:-}" ]]; then
        cat > "${CLAUDE_HOME}/settings.json" <<EOF
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "${ANTHROPIC_AUTH_TOKEN}",
    "ANTHROPIC_BASE_URL": "${ANTHROPIC_BASE_URL}",
    "ANTHROPIC_MODEL": "${ANTHROPIC_MODEL:-}",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "${ANTHROPIC_DEFAULT_HAIKU_MODEL:-${ANTHROPIC_MODEL:-}}",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "${ANTHROPIC_DEFAULT_SONNET_MODEL:-${ANTHROPIC_MODEL:-}}",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "${ANTHROPIC_DEFAULT_OPUS_MODEL:-${ANTHROPIC_MODEL:-}}",
    "API_TIMEOUT_MS": "3000000",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
  }
}
EOF
        echo "[CI] settings.json 已写入（AUTH_TOKEN + BASE_URL=${ANTHROPIC_BASE_URL}, MODEL=${ANTHROPIC_MODEL:-<default>}）"
    elif [[ -n "${ANTHROPIC_API_KEY:-}" && -n "${ANTHROPIC_BASE_URL:-}" ]]; then
        cat > "${CLAUDE_HOME}/settings.json" <<EOF
{
  "env": {
    "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}",
    "ANTHROPIC_BASE_URL": "${ANTHROPIC_BASE_URL}",
    "ANTHROPIC_MODEL": "${ANTHROPIC_MODEL:-}",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "${ANTHROPIC_DEFAULT_HAIKU_MODEL:-${ANTHROPIC_MODEL:-}}",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "${ANTHROPIC_DEFAULT_SONNET_MODEL:-${ANTHROPIC_MODEL:-}}",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "${ANTHROPIC_DEFAULT_OPUS_MODEL:-${ANTHROPIC_MODEL:-}}",
    "API_TIMEOUT_MS": "3000000",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
  }
}
EOF
        echo "[CI] settings.json 已写入（API_KEY + BASE_URL=${ANTHROPIC_BASE_URL}, MODEL=${ANTHROPIC_MODEL:-<default>}）"
    elif [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
        echo "[CI] 使用 ANTHROPIC_API_KEY（官方 Anthropic API，无自定义 BASE_URL）"
    else
        echo "[ERROR] 未检测到有效鉴权：需要 Bedrock（CLAUDE_CODE_USE_BEDROCK=1 等）+ AWS 凭证，或 ANTHROPIC_AUTH_TOKEN+BASE_URL，或 ANTHROPIC_API_KEY"
        exit 1
    fi

    # ── Step 1: Prepare workspaces ────────────────────────────────────────────
    PREPARE_CMD=(
        python evaluation/scripts/devshell/run_devshell_eval.py
        --prepare-cc-baseline
        --run-label "${RUN_LABEL}"
        --modes ${MODES_ARGS}
        --eval-ingest-pending-only
        --no-clean-results
    )
    if [[ "${LAYOUT}" == "questions" ]]; then
        PREPARE_CMD+=(--questions "${Q_IDS[@]}")
    else
        PREPARE_CMD+=(--capabilities ${CAPS_ARGS})
        if [[ "${LIMIT}" -gt 0 ]]; then
            PREPARE_CMD+=(--limit "${LIMIT}")
        fi
    fi

    echo ""
    echo "[STEP 1] Prepare workspaces..."
    "${PREPARE_CMD[@]}"

    # ── 从 results/ 取最新 RUN_DIR ────────────────────────────────────────────
    RUN_DIR=$(find "${APP_DIR}/results" -maxdepth 1 -type d -name "${RUN_LABEL}_*" | sort | tail -1)
    if [[ -z "${RUN_DIR}" ]]; then
        echo "[ERROR] 找不到 RUN_DIR，prepare 可能失败"
        exit 1
    fi
    echo "[CI] RUN_DIR = ${RUN_DIR}"

    # ── Step 2: 跑题（claude -p）+ finalize ───────────────────────────────────
    RUN_CMD=(
        python evaluation/scripts/baseline/run_claude_cli_baseline_tasks.py
        --run-dir "${RUN_DIR}"
        --max-turns "${MAX_TURNS}"
        --timeout "${TIMEOUT_S}"
        --jobs "${CLAUDE_JOBS}"
        --skip-completed
    )
    if [[ -n "${MODEL}" ]]; then
        RUN_CMD+=(--model "${MODEL}")
    fi
    if [[ "${CLAUDE_PER_TASK_PIPELINE}" == "1" ]]; then
        RUN_CMD+=(--finalize-per-task)
        if [[ "${PENDING_ONLY}" == "1" ]]; then
            RUN_CMD+=(--eval-ingest-pending-only)
            if [[ "${BASELINE_SCORE_SUBMIT:-1}" == "1" ]]; then
                RUN_CMD+=(--score-submit-per-task)
                RUN_CMD+=(--score-ingest-timeout "${SCORE_INGEST_TIMEOUT}")
                if [[ -n "${BASELINE_SCORE_EVAL_CONFIG:-}" ]]; then
                    RUN_CMD+=(--eval-config "${BASELINE_SCORE_EVAL_CONFIG}")
                fi
            fi
        fi
    else
        if [[ "${PENDING_ONLY}" == "1" ]]; then
            RUN_CMD+=(--finalize --eval-ingest-pending-only)
        else
            RUN_CMD+=(--finalize)
        fi
    fi

    echo ""
    echo "[STEP 2] 跑题（claude -p）+ finalize..."
    CLAUDE_RUN_EXIT=0
    "${RUN_CMD[@]}" || CLAUDE_RUN_EXIT=$?

else
    echo "[ERROR] 未知的 EVAL_RUNNER: ${EVAL_RUNNER}（可选值: claude_cli, devshell）"
    exit 1
fi

# ── 统一解析 RUN_DIR（devshell 路径此前可能未设置）────────────────────────────
if [[ -z "${RUN_DIR:-}" ]]; then
    RUN_DIR=$(find "${APP_DIR}/results" -maxdepth 1 -type d -name "${RUN_LABEL}_*" | sort | tail -1)
fi

# ── 阶段二：BinaryEvaluator 评分 + ingest POST（见 baseline_cc_eval.md）──────
BASELINE_SCORE_SUBMIT="${BASELINE_SCORE_SUBMIT:-1}"
SCORE_TIMEOUT="${BASELINE_SCORE_EVAL_INGEST_TIMEOUT:-120}"
if [[ "${BASELINE_SCORE_SUBMIT}" == "1" && "${PENDING_ONLY}" == "1" && -n "${RUN_DIR:-}" ]]; then
    if [[ "${EVAL_RUNNER}" == "claude_cli" && "${BASELINE_CLAUDE_PER_TASK_PIPELINE:-1}" == "1" ]]; then
        echo ""
        echo "[INFO] claude_cli 已在 STEP 2 逐题评分并 POST（BASELINE_CLAUDE_PER_TASK_PIPELINE=1），跳过 STEP 3"
    else
        PENDING_DIR="${RUN_DIR}/pending_ingest"
        if [[ -d "${PENDING_DIR}" ]]; then
            shopt -s nullglob
            PENDING_JSONS=("${PENDING_DIR}"/*.json)
            shopt -u nullglob
            if ((${#PENDING_JSONS[@]} > 0)); then
                echo ""
                echo "[STEP 3] BinaryEvaluator 评分 + ingest 提交（score_baseline_tasks.py --submit）..."
                SCORE_CMD=(
                    python evaluation/scripts/baseline/score_baseline_tasks.py
                    --run-dir "${RUN_DIR}"
                    --submit
                    --eval-ingest-timeout "${SCORE_TIMEOUT}"
                )
                if [[ -n "${BASELINE_SCORE_EVAL_CONFIG:-}" ]]; then
                    SCORE_CMD+=(--eval-config "${BASELINE_SCORE_EVAL_CONFIG}")
                fi
                "${SCORE_CMD[@]}"
            else
                echo "[WARN] pending_only=1 但 pending_ingest 下无 .json，跳过阶段二"
            fi
        fi
    fi
fi

# ══════════════════════════════════════════════════════════════════════════════
#  通用汇总统计
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "[汇总] 查找产物..."

if [[ -n "${RUN_DIR:-}" ]]; then
    echo "  RUN_DIR: ${RUN_DIR}"
    # 写入 dotenv 供 CI artifact 使用
    echo "BASELINE_RUN_DIR=${RUN_DIR}" >> "${APP_DIR}/baseline_run.env"

    RAW_RUNS="${RUN_DIR}/raw_runs.jsonl"
    if [[ -f "${RAW_RUNS}" ]]; then
        TOTAL=$(wc -l < "${RAW_RUNS}")
        echo "  raw_runs.jsonl: ${TOTAL} 行"
    fi

    PENDING_DIR="${RUN_DIR}/pending_ingest"
    if [[ -d "${PENDING_DIR}" ]]; then
        PENDING_COUNT=$(find "${PENDING_DIR}" -name "*.json" | wc -l)
        echo "  pending_ingest: ${PENDING_COUNT} 个待阅卷文件"
        echo ""
        echo "  待阅卷 task 列表："
        find "${PENDING_DIR}" -name "*.json" | sort | while read -r f; do
            echo "    - $(basename "${f}")"
        done
    fi
else
    echo "  [WARN] 未找到 RUN_DIR，跳过汇总"
fi

echo ""
echo "=== 评测完成（${EVAL_RUNNER}）==="
echo "  查看详细产物请下载 CI artifact: results/"

# 传递跑题阶段退出码（部分失败/超时仍先写入 baseline_run.env 与汇总，便于 docker cp artifact）
if [[ "${EVAL_RUNNER}" == "devshell" && "${DEVSHELL_EXIT:-0}" -ne 0 ]]; then
    echo "[CI] devshell 有题目失败，退出码: ${DEVSHELL_EXIT}"
    exit "${DEVSHELL_EXIT}"
fi
if [[ "${EVAL_RUNNER}" == "claude_cli" && "${CLAUDE_RUN_EXIT:-0}" -ne 0 ]]; then
    echo "[CI] Claude CLI baseline 有失败/超时，退出码: ${CLAUDE_RUN_EXIT}"
    exit "${CLAUDE_RUN_EXIT}"
fi
