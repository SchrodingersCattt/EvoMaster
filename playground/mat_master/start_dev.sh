#!/bin/bash
# One-click start: backend (FastAPI) + frontend (Next.js).
# Run from EvoMaster project root.
#
# 端口：BACKEND_PORT（默认 50001，冲突时自动回退到 50011）、FRONTEND_PORT（默认 50004）。
# 公网访问：设置 PUBLIC_HOST 后，API/WS 将使用该 host。
# 例: PUBLIC_HOST=gjao1318755.bohrium.tech ./start_dev.sh

set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Windows (Git Bash / MINGW) 上 50001 易触发 WinError 10013，后端改用 8000
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || -n "$MSYSTEM" ]]; then
  IS_WIN=1
else
  IS_WIN=
fi
if [[ -n "$IS_WIN" && -z "${BACKEND_PORT+x}" ]]; then
  export BACKEND_PORT="${BACKEND_PORT:-8000}"
fi

# 端口（支持环境变量 BACKEND_PORT、FRONTEND_PORT 覆盖）
BACKEND_PORT_EXPLICIT=
FRONTEND_PORT_EXPLICIT=
if [[ -n "${BACKEND_PORT+x}" ]]; then
  BACKEND_PORT_EXPLICIT=1
fi
if [[ -n "${FRONTEND_PORT+x}" ]]; then
  FRONTEND_PORT_EXPLICIT=1
fi

BACKEND_PORT="${BACKEND_PORT:-50001}"
FRONTEND_PORT="${FRONTEND_PORT:-50004}"
BACKEND_FALLBACK_PORT="${BACKEND_FALLBACK_PORT:-50011}"
FRONTEND_FALLBACK_PORT="${FRONTEND_FALLBACK_PORT:-50014}"

# === 0. 端口选择：只清理 mat_master 自己的旧进程，避免误杀其他服务 ===
echo "Checking ports for MatMaster dev services..."

port_pids() {
  if ! command -v lsof >/dev/null 2>&1; then
    return 0
  fi
  lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

pid_command() {
  ps -p "$1" -o command= 2>/dev/null || true
}

is_owned_pid() {
  local pid=$1
  local role=$2
  local cmd
  cmd=$(pid_command "$pid")
  if [[ "$role" == "backend" ]]; then
    [[ "$cmd" == *"playground.mat_master.service.server"* ]]
    return
  fi
  [[ "$cmd" == *"playground/mat_master/frontend"* || "$cmd" == *"next dev"* || "$cmd" == *"/next"* ]]
}

release_owned_port() {
  local port=$1
  local role=$2
  local found_foreign=
  local released=
  local pid

  for pid in $(port_pids "$port"); do
    if is_owned_pid "$pid" "$role"; then
      kill -9 "$pid" 2>/dev/null || true
      released=1
    else
      found_foreign=1
    fi
  done

  if [ -n "$released" ]; then
    echo "  -> Released existing MatMaster $role on port $port" >&2
    sleep 1
  fi

  if [ -n "$(port_pids "$port")" ]; then
    if [ -n "$found_foreign" ]; then
      return 1
    fi
    return 1
  fi
  return 0
}

resolve_port() {
  local role=$1
  local preferred=$2
  local fallback=$3
  local explicit=$4

  if release_owned_port "$preferred" "$role"; then
    echo "$preferred"
    return 0
  fi

  if [ -n "$explicit" ]; then
    echo "ERROR: ${role} port $preferred is occupied by another service. Please choose a different port." >&2
    exit 1
  fi

  echo "  -> Preferred ${role} port $preferred is occupied by another service; falling back to $fallback" >&2
  if release_owned_port "$fallback" "$role"; then
    echo "$fallback"
    return 0
  fi

  echo "ERROR: Both ${role} ports $preferred and $fallback are unavailable." >&2
  exit 1
}

BACKEND_PORT="$(resolve_port backend "$BACKEND_PORT" "$BACKEND_FALLBACK_PORT" "$BACKEND_PORT_EXPLICIT")"
FRONTEND_PORT="$(resolve_port frontend "$FRONTEND_PORT" "$FRONTEND_FALLBACK_PORT" "$FRONTEND_PORT_EXPLICIT")"

# === 1. 获取 IP / 公网域名 (用于提示与前端 API 地址) ===
# hostname -I 仅 Linux 支持；Windows/Git Bash 用 127.0.0.1
SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -z "$SERVER_IP" ]; then SERVER_IP="127.0.0.1"; fi
# 公网地址由环境变量 PUBLIC_HOST 控制；未设置时用本机 IP
PUBLIC_HOST="${PUBLIC_HOST:-$SERVER_IP}"

echo "----------------------------------------------------------------"
echo "Starting MatMaster Dev Environment"
echo "Project Root: $ROOT"
echo "Server IP   : $SERVER_IP"
echo "Public Host : $PUBLIC_HOST (for API/WS, set PUBLIC_HOST for 公网)"
echo "----------------------------------------------------------------"

# === 2. 前端 API/WS 地址 ===
# 由 PUBLIC_HOST 或显式 NEXT_PUBLIC_API_URL 控制

if [ -z "$NEXT_PUBLIC_API_URL" ]; then
    export NEXT_PUBLIC_API_URL="http://${PUBLIC_HOST}:${BACKEND_PORT}"
    export NEXT_PUBLIC_WS_URL="ws://${PUBLIC_HOST}:${BACKEND_PORT}/ws/chat"
    echo "⚠️  Auto-configured API URL to: $NEXT_PUBLIC_API_URL"
else
    echo "✅ Using provided API URL: $NEXT_PUBLIC_API_URL"
fi

# === 3. Backend: FastAPI（仓库根目录 uv run，与 README_WEB / AGENTS 一致）===
echo "Starting backend (MatMaster Local Web, FastAPI) on 0.0.0.0:${BACKEND_PORT}..."
cd "$ROOT"
export BACKEND_PORT
uv run python -m playground.mat_master.service.server &
BACKEND_PID=$!

# === 4. Frontend: Next.js (监听 0.0.0.0) ===
echo "Preparing frontend..."
cd "$ROOT/playground/mat_master/frontend"
if [ ! -d "node_modules" ]; then
  echo "Running npm install (first time)..."
  npm install
fi

echo "Starting frontend (Next.js) on 0.0.0.0:${FRONTEND_PORT}..."
# -H 0.0.0.0 让 Next.js 接受外网访问
npm run dev -- -H 0.0.0.0 -p "$FRONTEND_PORT" &
FRONTEND_PID=$!
cd "$ROOT"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" EXIT INT TERM

echo ""
echo "================================================================"
echo "  🚀 Service Running!"
echo ""
echo "  👉 Dashboard (Browser): http://$PUBLIC_HOST:$FRONTEND_PORT  (or http://$SERVER_IP:$FRONTEND_PORT)"
echo "  👉 Backend API        : $NEXT_PUBLIC_API_URL"
echo ""
echo "  按 Ctrl+C 停止服务"
echo "================================================================"
wait
