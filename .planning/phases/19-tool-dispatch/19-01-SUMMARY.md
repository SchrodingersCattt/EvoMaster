---
phase: 19-tool-dispatch
plan: 01
subsystem: infra
tags: [asyncio, event-loop, daemon-thread, run_coroutine_threadsafe, bridge]

# Dependency graph
requires:
  - phase: 18-exp
    provides: "Exp lifecycle async (build_runtime, _run_cleanup_callbacks)"
  - phase: 17-agentkernel
    provides: "AgentKernel.run() async"
  - phase: 16-messagebus-eventrouter
    provides: "EventRouter async start/stop"
provides:
  - "Unified single event loop bridge in agent_run_service.py"
  - "DevShell asyncio.run() bridge pattern"
  - "Robust cleanup order: Bohrium -> Exp -> Router -> loop stop"
affects: [19-tool-dispatch plan 02, service-layer]

# Tech tracking
tech-stack:
  added: []
  patterns: ["run_coroutine_threadsafe unified bridge", "asyncio.run single-shot bridge"]

key-files:
  created: []
  modified:
    - src/services/agent_run_service.py
    - matmaster/devshell/runner.py

key-decisions:
  - "Single daemon thread event loop replaces dual-loop architecture (D-01)"
  - "Cleanup order: Bohrium -> Exp -> Router -> loop, each step guarded by existence check"
  - "DevShell uses asyncio.run() instead of daemon thread pattern (D-07)"

patterns-established:
  - "run_coroutine_threadsafe bridge: all async matmaster calls from sync service layer go through a single daemon thread event loop"
  - "asyncio.run single-shot bridge: DevShell and similar sync entry points use asyncio.run() for clean single-shot async execution"

requirements-completed: [BRDG-01, BRDG-02]

# Metrics
duration: 7min
completed: 2026-03-29
---

# Phase 19 Plan 01: Unified Loop Bridge Summary

**Single daemon thread event loop replacing dual-loop architecture in agent_run_service.py, plus asyncio.run() bridge for DevShell**

## Performance

- **Duration:** 7 min
- **Started:** 2026-03-29T14:45:27Z
- **Completed:** 2026-03-29T14:52:49Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Unified agent_run_service.py from dual event loop (_router_loop + _loop) to single daemon thread + run_forever pattern
- All async calls (router.start, build_runtime, kernel.run, cleanup, router.stop) dispatched via run_coroutine_threadsafe to unified _loop
- Robust cleanup ordering with variable existence guards for partial initialization failure
- Simplified DevShell runner.py from manual loop management to asyncio.run()
- 1057 tests pass with 0 failures

## Task Commits

Each task was committed atomically:

1. **Task 1: Unify agent_run_service.py to single daemon thread event loop** - `e1be872` (feat)
2. **Task 2: Simplify DevShell runner.py bridge to asyncio.run()** - `d28a170` (feat)

## Files Created/Modified
- `src/services/agent_run_service.py` - Unified loop bridge: single daemon thread runs all async operations, 6 run_coroutine_threadsafe calls on _loop
- `matmaster/devshell/runner.py` - asyncio.run() bridge: inner async _run_once() handles build_runtime -> kernel.run -> cleanup lifecycle

## Decisions Made
- Used single daemon thread event loop (name="agent-loop") for all async operations in run_agent_sync(), eliminating the separate router-loop thread
- Cleanup order: Bohrium first (can still emit events) -> Exp cleanup (via run_coroutine_threadsafe with 30s timeout) -> Router stop (drains final events, 10s timeout) -> loop stop + thread join
- Each cleanup step guarded by `if 'exp' in dir()` / `if '_loop' in dir()` to handle partial initialization failures gracefully
- Quota path (use_quota) left unchanged -- uses FastAPI loop parameter, separate from agent's _loop per D-01
- DevShell uses asyncio.run() for simplicity (D-07) -- no need for daemon thread complexity in development tool

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

Prerequisite phases (17-agentkernel, 18-exp) with async method conversions existed on the refactor/async-matmaster branch but not on the worktree base. Resolved by merging refactor/async-matmaster into the worktree branch before executing the plan.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Unified loop bridge is ready for plan 02 (parallel tool dispatch) -- all async operations share the same event loop
- EventRouter and Kernel now run on the same loop, enabling asyncio primitive sharing
- DevShell bridge pattern established for any future sync entry points

## Self-Check: PASSED

- FOUND: src/services/agent_run_service.py
- FOUND: matmaster/devshell/runner.py
- FOUND: .planning/phases/19-tool-dispatch/19-01-SUMMARY.md
- FOUND: e1be872 (Task 1 commit)
- FOUND: d28a170 (Task 2 commit)

---
*Phase: 19-tool-dispatch*
*Completed: 2026-03-29*
