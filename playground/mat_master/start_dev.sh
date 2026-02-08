#!/bin/bash
# One-click start: backend (FastAPI :8000) + frontend (Next.js :3000).
# Run from EvoMaster project root. Optional: conda activate evomaster

set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Starting MatMaster (one-click)..."
echo "Project root: $ROOT"

# Backend
echo "Starting backend (FastAPI) on :8000..."
cd "$ROOT/playground/mat_master/service"
python server.py &
BACKEND_PID=$!
cd "$ROOT"

# Frontend: ensure deps then start
echo "Preparing frontend..."
cd "$ROOT/playground/mat_master/frontend"
if [ ! -d "node_modules" ]; then
  echo "Running npm install (first time)..."
  npm install
fi
echo "Starting frontend (Next.js) on :3000..."
npm run dev &
FRONTEND_PID=$!
cd "$ROOT"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" EXIT INT TERM

echo ""
echo "  Dashboard: http://localhost:3000"
echo "  API/Embed: http://localhost:8000"
echo "  Ctrl+C to stop both."
echo ""
wait
