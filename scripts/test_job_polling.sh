#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# 用法：
#   scripts/test_job_polling.sh
# 或：
#   scripts/test_job_polling.sh "https://.../graphene.cif"   # 可选覆盖默认 URL
#
# 环境变量（可选）：
#   API_BASE        默认 http://127.0.0.1:8000/api/v1
#   USER_ID         默认 3656033
#   SESSION_PREFIX  默认 demo-dp-graphene-rerun
#   SSE_MAX_TIME    默认 240（秒）
#   WAIT_TIMEOUT    默认 180（秒）
#   HEALTH_WAIT     默认 30（秒），启动前等待服务健康
#   AUTO_START_SERVER 默认 1；若服务不可达则自动拉起 uvicorn，脚本结束后自动关闭
#   UVICORN_BIN     默认 ./.venv/bin/uvicorn
#   APP_MODULE      默认 app:app
#   TASK_PROMPT     自定义任务提示词
#   GRAPHENE_FILE_URL  默认空（仅文本建模）；可传 URL 覆盖
#   FAKE_LLM_LOG_PATH 可选，指定伪造日志文件；不传则用内置故障日志模板
#   LOG_DIR         默认 ./logs/job_polling_tests
#   INJECT_DIR      默认 ./logs/monitor_job_injections（monitor_job 测试注入目录）
#   INJECT_FAKE_LOG 默认 1；在拿到 bohr_job_id 后注入伪造故障日志
#   INJECT_DELAY    默认 1（秒）；拿到 bohr_job_id 后多久写入注入日志

API_BASE="${API_BASE:-http://127.0.0.1:8002/api/v1}"
USER_ID="${USER_ID:-3656033}"
SESSION_PREFIX="${SESSION_PREFIX:-demo-dp-graphene-rerun}"
SSE_MAX_TIME="${SSE_MAX_TIME:-240}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-180}"
HEALTH_WAIT="${HEALTH_WAIT:-30}"
LOG_DIR="${LOG_DIR:-./logs/job_polling_tests}"
TASK_PROMPT="${TASK_PROMPT:-石墨烯建模并进行dp 弛豫}"
INJECT_DIR="${INJECT_DIR:-./logs/monitor_job_injections}"
INJECT_FAKE_LOG="${INJECT_FAKE_LOG:-1}"  # 是否在任务运行中注入伪造日志
INJECT_DELAY="${INJECT_DELAY:-1}"  # 拿到 bohr_job_id 后多久执行注入
FAKE_LLM_LOG_PATH="${FAKE_LLM_LOG_PATH:-}"
AUTO_START_SERVER="${AUTO_START_SERVER:-1}"
UVICORN_BIN="${UVICORN_BIN:-./.venv/bin/uvicorn}"
APP_MODULE="${APP_MODULE:-app:app}"

DEFAULT_GRAPHENE_FILE_URL=""
GRAPHENE_FILE_URL="${GRAPHENE_FILE_URL:-${1:-${DEFAULT_GRAPHENE_FILE_URL}}}"

mkdir -p "${LOG_DIR}"
mkdir -p "${INJECT_DIR}"

SESSION_ID="${SESSION_PREFIX}-$(date +%s)-$RANDOM"
SSE_OUT="${LOG_DIR}/sse_${SESSION_ID}.out"
INJECT_OUT="${LOG_DIR}/inject_${SESSION_ID}.out"
PAYLOAD_FILE="${LOG_DIR}/payload_${SESSION_ID}.json"
SERVER_LOG="${LOG_DIR}/uvicorn_${SESSION_ID}.log"
SSE_PID=""
INJECT_PID=""
SERVER_PID=""
SERVER_STARTED_BY_SCRIPT=0

python - "${TASK_PROMPT}" "${GRAPHENE_FILE_URL}" > "${PAYLOAD_FILE}" <<'PY'
import json
import sys

prompt = sys.argv[1]
file_url = sys.argv[2]
payload = {"content": prompt}
if file_url.strip():
    payload["files"] = [file_url.strip()]
print(json.dumps(payload, ensure_ascii=False))
PY

echo "== Session ID: ${SESSION_ID}"
if [[ -n "${GRAPHENE_FILE_URL}" ]]; then
  echo "== Graphene file: ${GRAPHENE_FILE_URL}"
else
  echo "== Graphene file: (none, 仅文本建模)"
fi
echo "== SSE output: ${SSE_OUT}"
echo "== Inject output: ${INJECT_OUT}"
echo "== Server log: ${SERVER_LOG}"
echo "== Inject dir: ${INJECT_DIR}"

HEALTH_URL="${API_BASE%/api/v1}/api/health"
echo "== 等待服务健康: ${HEALTH_URL} (max ${HEALTH_WAIT}s)"

health_ok() {
  curl -fsS "${HEALTH_URL}" >/dev/null 2>&1
}

if ! health_ok; then
  if [[ "${AUTO_START_SERVER}" == "1" ]]; then
    SERVER_HOST_PORT="$(python - "${API_BASE}" <<'PY'
from urllib.parse import urlparse
import sys

url = sys.argv[1]
root = url.replace('/api/v1', '')
p = urlparse(root)
host = p.hostname or '127.0.0.1'
port = p.port or (443 if p.scheme == 'https' else 80)
print(f"{host} {port}")
PY
)"
    read -r SERVER_HOST SERVER_PORT <<< "${SERVER_HOST_PORT}"
    echo "== 服务不可达，自动启动 uvicorn: ${SERVER_HOST}:${SERVER_PORT}"
    if [[ -x "${UVICORN_BIN}" ]]; then
      LOG_DIR=./logs MONITOR_JOB_INJECT_DIR="${INJECT_DIR}" "${UVICORN_BIN}" "${APP_MODULE}" --host "${SERVER_HOST}" --port "${SERVER_PORT}" > "${SERVER_LOG}" 2>&1 &
    else
      LOG_DIR=./logs MONITOR_JOB_INJECT_DIR="${INJECT_DIR}" python -m uvicorn "${APP_MODULE}" --host "${SERVER_HOST}" --port "${SERVER_PORT}" > "${SERVER_LOG}" 2>&1 &
    fi
    SERVER_PID=$!
    SERVER_STARTED_BY_SCRIPT=1
  fi
fi

HEALTH_DEADLINE=$(( $(date +%s) + HEALTH_WAIT ))
until health_ok; do
  if [[ $(date +%s) -ge ${HEALTH_DEADLINE} ]]; then
    echo "ERROR: 服务健康检查超时，无法连接 ${HEALTH_URL}"
    exit 3
  fi
  sleep 1
done
echo "== 服务健康检查通过"

curl -sS -N --max-time "${SSE_MAX_TIME}" -X POST \
  "${API_BASE}/chat/sessions/${SESSION_ID}/stream" \
  -H "Content-Type: application/json" \
  -H "X-User-Id: ${USER_ID}" \
  --data @"${PAYLOAD_FILE}" > "${SSE_OUT}" &
SSE_PID=$!

cleanup() {
  if [[ -n "${SSE_PID}" ]] && kill -0 "${SSE_PID}" >/dev/null 2>&1; then
    kill "${SSE_PID}" >/dev/null 2>&1 || true
  fi
  if [[ "${SERVER_STARTED_BY_SCRIPT}" == "1" ]] && [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" >/dev/null 2>&1; then
    kill "${SERVER_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "== 等待 bohr_job_id（意味着弛豫提交成功并开始监控）..."
BOHR_JOB_ID=""
JOB_ID=""
DEADLINE=$(( $(date +%s) + WAIT_TIMEOUT ))
while [[ $(date +%s) -lt ${DEADLINE} ]]; do
  if [[ -s "${SSE_OUT}" ]]; then
    BOHR_JOB_ID="$(python - "${SSE_OUT}" <<'PY'
import re
import sys

path = sys.argv[1]
text = open(path, "r", encoding="utf-8", errors="ignore").read()
matches = re.findall(r'"bohr_job_id"\s*:\s*"([^"]+)"', text)
print(matches[-1] if matches else "")
PY
)"
    JOB_ID="$(python - "${SSE_OUT}" <<'PY'
import re
import sys

path = sys.argv[1]
text = open(path, "r", encoding="utf-8", errors="ignore").read()
matches = re.findall(r'"job_id"\s*:\s*"([^"]+)"', text)
print(matches[-1] if matches else "")
PY
)"
    if [[ -n "${BOHR_JOB_ID}" ]]; then
      break
    fi
  fi
  if ! kill -0 "${SSE_PID}" >/dev/null 2>&1; then
    echo "ERROR: SSE 请求已提前结束，且未获取到 bohr_job_id。请检查 ${SSE_OUT}"
    exit 4
  fi
  sleep 2
done

if [[ -z "${BOHR_JOB_ID}" ]]; then
  echo "ERROR: 在 ${WAIT_TIMEOUT}s 内未获取到 bohr_job_id。请检查 ${SSE_OUT}"
  exit 2
fi

echo "== 已获取 bohr_job_id: ${BOHR_JOB_ID}"
if [[ -n "${JOB_ID}" ]]; then
  echo "== 已获取 job_id: ${JOB_ID}"
fi

# 如果启用日志注入，在后台等待指定时间后写入 monitor_job 注入文件
if [[ "${INJECT_FAKE_LOG}" == "1" ]]; then
  {
    echo "== 日志注入任务：将在 ${INJECT_DELAY}s 后执行"
    sleep "${INJECT_DELAY}"

    echo "== 开始写入注入日志..."
    python - "${BOHR_JOB_ID}" "${FAKE_LLM_LOG_PATH}" "${INJECT_DIR}" <<'PY'
import sys
from pathlib import Path

bohr_job_id = (sys.argv[1] or "").strip()
fake_log_path = (sys.argv[2] or "").strip()
inject_dir = Path((sys.argv[3] or "").strip()).resolve()

if not bohr_job_id:
    print("ERROR: bohr_job_id is required")
    raise SystemExit(1)

default_fake_log = """
[INJECTED FAILURE LOG]
Step     Time          Energy          fmax
BFGS:   10 18:18:14      -27.015433        9.338106
BFGS:   11 18:18:15       -9.883741       25.214603
BFGS:   12 18:18:15      -28.106942       18.772994
BFGS:   13 18:18:15       35.294107       56.993201
BFGS:   14 18:18:15      -31.770245       73.221904
BFGS:   15 18:18:15      126.420553      118.024665
BFGS:   16 18:18:16      -48.007194      244.910388
BFGS:   17 18:18:16      904.332116      602.449901
BFGS:   18 18:18:16     -312.551084     1180.336522
""".strip()

if fake_log_path:
    try:
        fake_log = Path(fake_log_path).read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        fake_log = f"{default_fake_log}\n[WARN] read fake log path failed: {e}"
else:
    fake_log = default_fake_log

inject_dir.mkdir(parents=True, exist_ok=True)
inject_path = inject_dir / f"{bohr_job_id}.log.inject"
inject_path.write_text(fake_log.strip() + "\n", encoding="utf-8")
print(f"✓ 已写入 monitor_job 注入日志: {inject_path}")
print("  monitor_job 会在下一次 LLM 决策前读取该文件（默认一次性消费）")
PY
    echo "== 日志注入任务完成"
  } | tee -a "${INJECT_OUT}" &
  INJECT_PID=$!
fi

echo "== 注意：monitor_job 工具会自动进行 LLM 决策（llm_decision_mode=auto_terminate）"
echo "== 本脚本不主动轮询状态；仅记录 SSE，等待 agent 自行监控并决策"
echo ""

if [[ -n "${INJECT_PID}" ]]; then
  wait "${INJECT_PID}" || true
fi

echo "== 等待 SSE 结束（最长 ${SSE_MAX_TIME}s）..."
SSE_EXIT=0
wait "${SSE_PID}" || SSE_EXIT=$?
if [[ "${SSE_EXIT}" -ne 0 ]]; then
  echo "WARN: SSE 进程退出码=${SSE_EXIT}（可能是超时或网络中断）"
fi

TERMINATE_HIT="$(python - "${SSE_OUT}" <<'PY'
import re
import sys
text = open(sys.argv[1], "r", encoding="utf-8", errors="ignore").read()
hit = bool(re.search(r'"decision"\s*:\s*"terminate"', text, flags=re.IGNORECASE))
print("1" if hit else "0")
PY
)"

echo "== 完成"
echo "SSE:  ${SSE_OUT}"
echo "INJECT: ${INJECT_OUT}"
if [[ "${TERMINATE_HIT}" == "1" ]]; then
  echo "== 在 SSE 中检测到 LLM terminate 决策"
else
  echo "== 未在 SSE 中检测到 terminate 决策（请人工查看 SSE 详情）"
fi
echo ""
echo "== 说明："
echo "   monitor_job 工具内部会自动执行 LLM 决策（默认 auto_terminate）"
echo "   本脚本通过 ${INJECT_DIR}/<bohr_job_id>.log.inject 注入故障日志，模拟运行中日志突变"
echo "   查看 SSE 输出中的 llm_decision_history / termination 字段可判断是否提前终止"
