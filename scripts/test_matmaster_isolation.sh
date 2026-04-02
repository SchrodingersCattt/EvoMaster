#!/usr/bin/env bash
# scripts/test_matmaster_isolation.sh
# Prove matmaster can run its tests independently without evomaster/ and src/
# Phase 30 -- QUAL-07 isolation test
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Atomic safety: trap ensures restoration even if tests crash
cleanup() {
    [ -d _evomaster_hidden ] && mv _evomaster_hidden evomaster
    [ -d _src_hidden ] && mv _src_hidden src
    echo "[isolation] Restored evomaster/ and src/"
}
trap cleanup EXIT

echo "[isolation] Hiding evomaster/ and src/ ..."
[ -d evomaster ] && mv evomaster _evomaster_hidden
[ -d src ] && mv src _src_hidden
echo "[isolation] Hidden directories that exist"

# Run tests/matmaster/ full suite (D-02: no subset filtering)
echo "[isolation] Running tests/matmaster/ full suite ..."
uv run --extra dev python -m pytest tests/matmaster/ -x -q --tb=short 2>&1

echo "[isolation] All tests passed with evomaster/ and src/ hidden"
# cleanup triggered by trap on EXIT
