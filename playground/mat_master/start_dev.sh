#!/bin/bash
# One-click start: backend (FastAPI :8000) + frontend (Next.js :3000).
# Run from EvoMaster project root.

set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# === 0. 获取公网IP (可选，用于提示) ===
# 尝试自动获取服务器IP，方便你复制
SERVER_IP=$(hostname -I | awk '{print $1}')
if [ -z "$SERVER_IP" ]; then SERVER_IP="YOUR_SERVER_IP"; fi

echo "----------------------------------------------------------------"
echo "Starting MatMaster Dev Environment"
echo "Project Root: $ROOT"
echo "Server IP   : $SERVER_IP"
echo "----------------------------------------------------------------"

# === 1. 检查环境变量 (关键！) ===
# 如果没有设置 NEXT_PUBLIC_API_URL，前端会默认连 localhost，导致远程访问失败。
# 这里我们强制让用户确认，或者自动设置为服务器IP。

if [ -z "$NEXT_PUBLIC_API_URL" ]; then
    export NEXT_PUBLIC_API_URL="http://$SERVER_IP:8000"
    export NEXT_PUBLIC_WS_URL="ws://$SERVER_IP:8000/ws/chat"
    echo "⚠️  Auto-configured API URL to: $NEXT_PUBLIC_API_URL"
else
    echo "✅ Using provided API URL: $NEXT_PUBLIC_API_URL"
fi

# === 2. Backend: FastAPI (强制监听 0.0.0.0) ===
echo "Starting backend (FastAPI) on 0.0.0.0:8000..."
cd "$ROOT/playground/mat_master/service"

# 激活虚拟环境 (确保路径对)
if [ -f "$ROOT/.venv/bin/activate" ]; then
    source "$ROOT/.venv/bin/activate"
fi

# 【修改点】直接用 uvicorn 命令启动，强制 host=0.0.0.0，避免代码写死 localhost
# 假设 server.py 里有一个 app 对象
uvicorn server:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!
cd "$ROOT"

# === 3. Frontend: Next.js (监听 0.0.0.0) ===
echo "Preparing frontend..."
cd "$ROOT/playground/mat_master/frontend"
if [ ! -d "node_modules" ]; then
  echo "Running npm install (first time)..."
  npm install
fi

echo "Starting frontend (Next.js) on 0.0.0.0:3000..."
# -H 0.0.0.0 让 Next.js 接受外网访问
npm run dev -- -H 0.0.0.0 &
FRONTEND_PID=$!
cd "$ROOT"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" EXIT INT TERM

echo ""
echo "================================================================"
echo "  🚀 Service Running!"
echo ""
echo "  👉 Dashboard (Browser): http://$SERVER_IP:3000"
echo "  👉 Backend API        : $NEXT_PUBLIC_API_URL"
echo ""
echo "  按 Ctrl+C 停止服务"
echo "================================================================"
wait