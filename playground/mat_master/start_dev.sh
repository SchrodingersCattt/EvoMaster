#!/bin/bash
# One-click start: backend (FastAPI) + frontend (Next.js).
# Run from EvoMaster project root.
#
# 端口：BACKEND_PORT（默认 50001）、FRONTEND_PORT（默认 50003），支持环境变量覆盖。
# 公网访问：设置 PUBLIC_HOST 后，API/WS 将使用该 host。
# 例: PUBLIC_HOST=gjao1318755.bohrium.tech ./start_dev.sh

set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# 端口（支持环境变量 BACKEND_PORT、FRONTEND_PORT 覆盖）
BACKEND_PORT="${BACKEND_PORT:-50001}"
FRONTEND_PORT="${FRONTEND_PORT:-50003}"

# === 0. 启动前释放端口，避免 Address already in use ===
for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  if fuser "$port/tcp" >/dev/null 2>&1; then
    echo "Releasing port $port..."
    fuser -k "$port/tcp" 2>/dev/null || true
    sleep 1
  fi
done

# === 1. 获取 IP / 公网域名 (用于提示与前端 API 地址) ===
SERVER_IP=$(hostname -I | awk '{print $1}')
if [ -z "$SERVER_IP" ]; then SERVER_IP="YOUR_SERVER_IP"; fi
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

# === 3. Backend: FastAPI (强制监听 0.0.0.0) ===
echo "Starting backend (FastAPI) on 0.0.0.0:${BACKEND_PORT}..."
cd "$ROOT/playground/mat_master/service"

# 激活虚拟环境 (确保路径对)
if [ -f "$ROOT/.venv/bin/activate" ]; then
    source "$ROOT/.venv/bin/activate"
fi

# 【修改点】直接用 uvicorn 命令启动，强制 host=0.0.0.0，避免代码写死 localhost
# 假设 server.py 里有一个 app 对象
uvicorn server:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload &
BACKEND_PID=$!
cd "$ROOT"

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