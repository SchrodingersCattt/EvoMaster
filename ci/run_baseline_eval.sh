#!/usr/bin/env bash
# CI 内部执行脚本：支持 Claude CLI baseline 和 MatMaster DevShell 两种评测模式
# 在项目 Docker 容器内运行，由 ci/baseline-eval-child.yml 的 job 调用。
#
# 模式选择（EVAL_RUNNER）：
#   claude_cli（默认） — prepare workspace → claude -p 跑题 → finalize
#   devshell           — run_devshell_eval.py 一步完成（MatMaster Agent 内部跑题）
#
# 环境变量（须在 GitLab CI Variables 中配置）：
#   通用:
#     EVAL_RUNNER                    — claude_cli 或 devshell，默认 claude_cli
#     MATMASTER_TOOLS_SERVER         — ingest 入库地址（必须）
#     MATMASTER_TOOLS_EVALUATION_BEARER — ingest Bearer token（必须）
#     BASELINE_CAPABILITIES          — 逗号分隔 capability，默认 structure_construction
#     BASELINE_MODES                 — direct/planner/direct planner，默认 direct
#     BASELINE_LIMIT                 — 每次最多跑几道题（0=不限），默认 0
#     BASELINE_MODEL                 — 模型标识（空=使用默认）
#     BASELINE_RUN_LABEL             — run 目录前缀，默认 baseline_cc
#     BASELINE_PENDING_ONLY          — 1=pending模式（人工阅卷），0=proxy自动入库，默认 1
#   Claude CLI 模式专用:
#     ANTHROPIC_API_KEY 或 ANTHROPIC_AUTH_TOKEN — Claude CLI 鉴权（二选一）
#     ANTHROPIC_BASE_URL             — Claude CLI 端点（如 MiniMax/gpugeek 兼容端点）
#     ANTHROPIC_MODEL                — Claude CLI 指定模型名
#     BASELINE_MAX_TURNS             — 每题最大对话轮数，默认 50
#     BASELINE_TIMEOUT               — 每题超时秒数，默认 900
#   DevShell 模式专用:
#     LITELLM_PROXY_API_KEY          — LiteLLM 鉴权（llm_config.yaml 中 ${LITELLM_PROXY_API_KEY}）
#     LITELLM_PROXY_API_BASE         — LiteLLM base_url

set -euo pipefail

APP_DIR="/app"
cd "${APP_DIR}"

# ── 通用参数解析 ─────────────────────────────────────────────────────────────
EVAL_RUNNER="${EVAL_RUNNER:-claude_cli}"
CAPABILITIES="${BASELINE_CAPABILITIES:-structure_construction}"
MODES="${BASELINE_MODES:-direct}"
LIMIT="${BASELINE_LIMIT:-0}"
MODEL="${BASELINE_MODEL:-}"
RUN_LABEL="${BASELINE_RUN_LABEL:-baseline_cc}"
PENDING_ONLY="${BASELINE_PENDING_ONLY:-1}"

echo "=== CI Baseline Eval ==="
echo "  runner       : ${EVAL_RUNNER}"
echo "  capabilities : ${CAPABILITIES}"
echo "  modes        : ${MODES}"
echo "  limit        : ${LIMIT} (0=无限制)"
echo "  model        : ${MODEL:-<默认>}"
echo "  run_label    : ${RUN_LABEL}"
echo "  pending_only : ${PENDING_ONLY}"

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
        --capabilities ${CAPS_ARGS}
        --no-clean-results
    )
    if [[ "${LIMIT}" -gt 0 ]]; then
        DEVSHELL_CMD+=(--limit "${LIMIT}")
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
    echo "  max_turns    : ${MAX_TURNS}"
    echo "  timeout(s)   : ${TIMEOUT_S}"
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

    if [[ -n "${ANTHROPIC_BASE_URL:-}" && -n "${ANTHROPIC_AUTH_TOKEN:-}" ]]; then
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
        echo "[ERROR] 未检测到有效鉴权：需要 ANTHROPIC_AUTH_TOKEN+BASE_URL 或 ANTHROPIC_API_KEY"
        exit 1
    fi

    # ── Step 1: Prepare workspaces ────────────────────────────────────────────
    PREPARE_CMD=(
        python evaluation/scripts/devshell/run_devshell_eval.py
        --prepare-cc-baseline
        --run-label "${RUN_LABEL}"
        --modes ${MODES_ARGS}
        --capabilities ${CAPS_ARGS}
        --eval-ingest-pending-only
        --no-clean-results
    )
    if [[ "${LIMIT}" -gt 0 ]]; then
        PREPARE_CMD+=(--limit "${LIMIT}")
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
        --skip-completed
    )
    if [[ -n "${MODEL}" ]]; then
        RUN_CMD+=(--model "${MODEL}")
    fi
    if [[ "${PENDING_ONLY}" == "1" ]]; then
        RUN_CMD+=(--finalize --eval-ingest-pending-only)
    else
        RUN_CMD+=(--finalize)
    fi

    echo ""
    echo "[STEP 2] 跑题（claude -p）+ finalize..."
    "${RUN_CMD[@]}"

else
    echo "[ERROR] 未知的 EVAL_RUNNER: ${EVAL_RUNNER}（可选值: claude_cli, devshell）"
    exit 1
fi

# ══════════════════════════════════════════════════════════════════════════════
#  通用汇总统计
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "[汇总] 查找产物..."

# devshell 模式下 RUN_DIR 尚未设置，需查找
if [[ -z "${RUN_DIR:-}" ]]; then
    RUN_DIR=$(find "${APP_DIR}/results" -maxdepth 1 -type d -name "${RUN_LABEL}_*" | sort | tail -1)
fi

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

# 传递 devshell 退出码（部分题目失败时仍需收集 artifact 后再退出）
if [[ "${EVAL_RUNNER}" == "devshell" && "${DEVSHELL_EXIT:-0}" -ne 0 ]]; then
    echo "[CI] devshell 有题目失败，退出码: ${DEVSHELL_EXIT}"
    exit "${DEVSHELL_EXIT}"
fi
