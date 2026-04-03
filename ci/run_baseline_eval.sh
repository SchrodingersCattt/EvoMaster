#!/usr/bin/env bash
# CI 内部执行脚本：prepare → claude 跑题 → finalize
# 在项目 Docker 容器内运行，由 ci/baseline-eval.yml 调用。
#
# 环境变量（须在 GitLab CI Variables 中配置）：
#   ANTHROPIC_API_KEY          — Claude CLI 鉴权（必须）
#   MATMASTER_TOOLS_SERVER     — ingest 入库地址（必须）
#   MATMASTER_TOOLS_EVALUATION_BEARER — ingest Bearer（必须）
#   LITELLM_PROXY_API_KEY      — evaluator_llm 鉴权（可选，LLM judge 用）
#   LITELLM_PROXY_API_BASE     — evaluator_llm base_url（可选）
#   BASELINE_CAPABILITIES      — 逗号分隔 capability，默认 structure_construction
#   BASELINE_MODES             — direct/planner/direct planner，默认 direct
#   BASELINE_LIMIT             — 每次最多跑几道题（0=不限），默认 0
#   BASELINE_MAX_TURNS         — 每题最大对话轮数，默认 50
#   BASELINE_TIMEOUT           — 每题超时秒数，默认 900
#   BASELINE_MODEL             — claude 模型（空=使用 CLI 默认）
#   BASELINE_RUN_LABEL         — run 目录前缀，默认 baseline_cc
#   BASELINE_PENDING_ONLY      — 1=pending模式（人工阅卷），0=proxy自动入库，默认 1

set -euo pipefail

APP_DIR="/app"
cd "${APP_DIR}"

# ── 参数解析 ──────────────────────────────────────────────────────────────────
CAPABILITIES="${BASELINE_CAPABILITIES:-structure_construction}"
MODES="${BASELINE_MODES:-direct}"
LIMIT="${BASELINE_LIMIT:-0}"
MAX_TURNS="${BASELINE_MAX_TURNS:-50}"
TIMEOUT_S="${BASELINE_TIMEOUT:-900}"
MODEL="${BASELINE_MODEL:-}"
RUN_LABEL="${BASELINE_RUN_LABEL:-baseline_cc}"
PENDING_ONLY="${BASELINE_PENDING_ONLY:-1}"

echo "=== CI Baseline Eval ==="
echo "  capabilities : ${CAPABILITIES}"
echo "  modes        : ${MODES}"
echo "  limit        : ${LIMIT} (0=无限制)"
echo "  max_turns    : ${MAX_TURNS}"
echo "  timeout(s)   : ${TIMEOUT_S}"
echo "  model        : ${MODEL:-<claude 默认>}"
echo "  run_label    : ${RUN_LABEL}"
echo "  pending_only : ${PENDING_ONLY}"
echo "======================================="

# ── 确认 claude CLI 可用（由 Dockerfile.eval 预装）────────────────────────────
if ! command -v claude &>/dev/null; then
    echo "[ERROR] claude CLI 不在 PATH。请确认使用 Dockerfile.eval 构建的镜像。"
    exit 1
fi
echo "[CI] claude CLI: $(claude --version 2>/dev/null || echo 'unknown')"

# ── 写入 claude settings.json（注入 MiniMax/代理 配置）────────────────────────
# Claude CLI 优先读 ~/.claude/settings.json 里的 env，而非系统环境变量
# CI 通过 GitLab Variables 注入 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_MODEL
if [[ -n "${ANTHROPIC_BASE_URL:-}" && -n "${ANTHROPIC_AUTH_TOKEN:-}" ]]; then
    CLAUDE_HOME="${HOME}/.claude"
    mkdir -p "${CLAUDE_HOME}"
    cat > "${CLAUDE_HOME}/settings.json" <<EOF
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "${ANTHROPIC_AUTH_TOKEN}",
    "ANTHROPIC_BASE_URL": "${ANTHROPIC_BASE_URL}",
    "ANTHROPIC_MODEL": "${ANTHROPIC_MODEL:-MiniMax-M2.7}",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "${ANTHROPIC_MODEL:-MiniMax-M2.7}",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "${ANTHROPIC_MODEL:-MiniMax-M2.7}",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "${ANTHROPIC_MODEL:-MiniMax-M2.7}",
    "API_TIMEOUT_MS": "3000000",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": 1
  }
}
EOF
    echo "[CI] claude settings.json 已写入（BASE_URL=${ANTHROPIC_BASE_URL}, MODEL=${ANTHROPIC_MODEL:-MiniMax-M2.7}）"
elif [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "[CI] 使用 ANTHROPIC_API_KEY（官方 Anthropic API）"
else
    echo "[ERROR] 未检测到 ANTHROPIC_BASE_URL+ANTHROPIC_AUTH_TOKEN 或 ANTHROPIC_API_KEY，Claude CLI 无法鉴权"
    exit 1
fi

# ── 激活 Python 环境 ─────────────────────────────────────────────────────────
source "${APP_DIR}/.venv/bin/activate"

# ── 构造 prepare 参数 ────────────────────────────────────────────────────────
# capability 参数：逗号分隔 → 空格分隔
CAPS_ARGS=$(echo "${CAPABILITIES}" | tr ',' ' ')
MODES_ARGS=$(echo "${MODES}" | tr ',' ' ')

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

# ── 从 results/ 取最新 RUN_DIR ──────────────────────────────────────────────
RUN_DIR=$(find "${APP_DIR}/results" -maxdepth 1 -type d -name "${RUN_LABEL}_*" | sort | tail -1)
if [[ -z "${RUN_DIR}" ]]; then
    echo "[ERROR] 找不到 RUN_DIR，prepare 可能失败"
    exit 1
fi
echo "[CI] RUN_DIR = ${RUN_DIR}"
# 写入 dotenv 供后续 job 读取（GitLab artifact）
echo "BASELINE_RUN_DIR=${RUN_DIR}" >> "${APP_DIR}/baseline_run.env"

# ── 构造 run 参数 ─────────────────────────────────────────────────────────────
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

# finalize 参数
if [[ "${PENDING_ONLY}" == "1" ]]; then
    RUN_CMD+=(--finalize --eval-ingest-pending-only)
else
    RUN_CMD+=(--finalize)
fi

echo ""
echo "[STEP 2] 跑题（claude -p）+ finalize..."
"${RUN_CMD[@]}"

# ── 汇总统计输出 ──────────────────────────────────────────────────────────────
echo ""
echo "[STEP 3] 汇总结果..."
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

echo ""
echo "=== Baseline 评测完成 ==="
echo "  RUN_DIR: ${RUN_DIR}"
echo "  查看详细产物请下载 CI artifact: results/"
