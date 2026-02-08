#!/bin/bash
# One-click start: backend (FastAPI :8000) + frontend (Next.js :3000).
# Run from EvoMaster project root.
# For server deployment: export NEXT_PUBLIC_API_URL and NEXT_PUBLIC_WS_URL (see bottom).

set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Starting MatMaster (one-click)..."
echo "Project root: $ROOT"


echo "Starting backend (FastAPI) on 0.0.0.0:8000..."
cd "$ROOT/playground/mat_master/service"
python server.py &
BACKEND_PID=$!
cd "$ROOT"

# === 3. Frontend: Next.js on 0.0.0.0:3000 ===
echo "Preparing frontend..."
cd "$ROOT/playground/mat_master/frontend"
if [ ! -d "node_modules" ]; then
  echo "Running npm install (first time)..."
  npm install
fi
echo "Starting frontend (Next.js) on 0.0.0.0:3000..."
npm run dev -- -H 0.0.0.0 &
FRONTEND_PID=$!
cd "$ROOT"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" EXIT INT TERM

# Show URLs (use env if set for server deployment)
API_URL="${NEXT_PUBLIC_API_URL:-http://localhost:8000}"
WS_URL="${NEXT_PUBLIC_WS_URL:-ws://localhost:8000/ws/chat}"
echo ""
echo "  Dashboard:  http://localhost:3000  (or http://<server-ip>:3000)"
echo "  API:        $API_URL"
echo "  WebSocket:  $WS_URL"
echo "  Ctrl+C to stop both."
echo ""
if [ -z "$NEXT_PUBLIC_API_URL" ]; then
  echo "  Deploy on server: export NEXT_PUBLIC_API_URL=http://<server-ip>:8000"
  echo "                    export NEXT_PUBLIC_WS_URL=ws://<server-ip>:8000/ws/chat"
  echo "                    Or set in playground/mat_master/frontend/.env.local"
  echo "                    Ensure firewall allows 3000 and 8000."
fi
echo ""
wait
