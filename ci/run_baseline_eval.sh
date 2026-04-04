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
#     BASELINE_LIMIT                 — 每次最多跑几道题（0=不限），默认 0
#     BASELINE_MODEL                 — 模型标识（空=使用默认）
#     BASELINE_RUN_LABEL             — run 目录前缀，默认 baseline_cc
#     BASELINE_PENDING_ONLY          — 1=pending模式（人工阅卷），0=proxy自动入库，默认 1
#   子流水线布局（由生成器 / docker -e 注入）:
#     BASELINE_EVAL_LAYOUT           — capabilities（默认）或 questions
#     BASELINE_QUESTIONS             — 逗号分隔 question id；覆盖 ci/baseline_eval_preset.yaml 的 question_ids
#   题库与布局预设（仓库内文件，见 ci/baseline_eval_preset.yaml）:
#     child_pipeline                 — capabilities | questions（可被 CI 变量 BASELINE_CHILD_PIPELINE 覆盖）
#     questions_mode                 — preset | score_summary_missing_cc（仅 yaml，见 ci/baseline_eval_preset.yaml）
#     question_ids                   — questions 布局下的题目列表（BASELINE_LIMIT 默认忽略；
#                                      questions_mode=score_summary_missing_cc 时 LIMIT>0 用于封顶缺分列表）
#   Claude CLI 模式专用:
#     ANTHROPIC_API_KEY 或 ANTHROPIC_AUTH_TOKEN — Claude CLI 鉴权（二选一）
#     ANTHROPIC_BASE_URL             — Claude CLI 端点（如 MiniMax/gpugeek 兼容端点）
#     ANTHROPIC_MODEL                — Claude CLI 指定模型名
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
#     行为: GET .../evaluation/questions/score-summary，只跑 claude_code_score 为 null 的题目
#       （与 preset 中 question_ids / BASELINE_QUESTIONS 交集）；交集为空则 exit 0。须 MATMASTER_TOOLS_*。
#     BASELINE_SCORE_SUMMARY_TIMEOUT — 可选，score-summary GET 超时秒数，默认 120

set -euo pipefail

APP_DIR="/app"
cd "${APP_DIR}"

if [[ -x "${APP_DIR}/.venv/bin/python" ]]; then
    PY="${APP_DIR}/.venv/bin/python"
else
    PY="python3"
fi

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
        if [[ "${LIMIT}" -gt 0 ]]; then
            FETCH_CMD+=(--limit "${LIMIT}")
        fi
        mapfile -t Q_IDS < <("${FETCH_CMD[@]}")
        rm -f "${PRE_IDS_FILE}"
        if [[ ${#Q_IDS[@]} -eq 0 ]]; then
            echo "[INFO] score-summary 与预设交集后无待跑题目（Claude Code 基线均已覆盖），退出 0。"
            exit 0
        fi
    else
        mapfile -t Q_IDS < <("${PY}" "${APP_DIR}/ci/baseline_eval_preset.py" list-ids)
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
