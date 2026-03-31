---
phase: 15-hook
plan: 03
subsystem: core-engine
tags: [asyncio, hooks, event-loop, bridge-loop, agent-kernel]

# Dependency graph
requires:
  - phase: 15-hook/15-01
    provides: "async run_* helpers and _sync_call_async bridge in agent.py"
  - phase: 15-hook/15-02
    provides: "ConfirmationHook Future-based async with per-run loop injection via set_loop"
provides:
  - "All _sync_call_async calls in agent.py use per-run _bridge_loop (no module-level fallback)"
  - "REQUIREMENTS.md HOOK-01 and HOOK-03 status updated to Complete"
affects: [phase-16, phase-17]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Consistent per-run bridge loop passing to _sync_call_async for loop-safe async bridging"

key-files:
  created: []
  modified:
    - matmaster/core/agent.py
    - .planning/REQUIREMENTS.md

key-decisions:
  - "Also pass _bridge_loop to tool_registry.execute call for consistency, even though tools do not use ConfirmationHook Future"

patterns-established:
  - "All _sync_call_async calls must pass the per-run _bridge_loop parameter, never rely on module-level default"

requirements-completed: [HOOK-01, HOOK-03]

# Metrics
duration: 5min
completed: 2026-03-27
---

# Phase 15 Plan 03: Gap Closure Summary

**Fix 14 _sync_call_async calls to pass per-run _bridge_loop, eliminating loop mismatch bug for ConfirmationHook re-enablement; REQUIREMENTS.md HOOK-01/HOOK-03 status synced to Complete**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-27T16:12:24Z
- **Completed:** 2026-03-27T16:17:10Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Fixed 14 `_sync_call_async` calls in agent.py (13 run_* + 1 tool_registry.execute) to pass per-run `_bridge_loop` parameter instead of using module-level default
- Eliminated potential RuntimeError ("Future attached to a different loop") that would occur when ConfirmationHook is re-enabled
- Updated REQUIREMENTS.md: HOOK-01 and HOOK-03 checkboxes, traceability table, and coverage statistics all synchronized to Complete status

## Task Commits

Each task was committed atomically:

1. **Task 1: Fix _sync_call_async _bridge_loop parameter passing** - `4cd7013` (fix)
2. **Task 2: Update REQUIREMENTS.md HOOK-01/HOOK-03 status** - `7379b52` (docs)

## Files Created/Modified
- `matmaster/core/agent.py` - Added `_bridge_loop` as second argument to all 14 `_sync_call_async` calls in `_run_loop` and `_do_stream_llm`
- `.planning/REQUIREMENTS.md` - Marked HOOK-01 and HOOK-03 as Complete in checkboxes, traceability table, and coverage statistics (13 -> 15)

## Decisions Made
- Included `tool_registry.execute` call in the fix (not just run_* calls) for consistency -- all `_sync_call_async` calls in the execution path now use the same per-run loop

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Known Stubs

None.

## Next Phase Readiness
- Phase 15 (Hook system async) is fully complete -- all 3 plans executed, all gaps closed
- Phase 16 (MessageBus + EventRouter async) can proceed
- ConfirmationHook can be safely re-enabled in production once Phase 17 converts Kernel to full async

## Self-Check: PASSED

All files and commits verified.

---
*Phase: 15-hook*
*Completed: 2026-03-27*
