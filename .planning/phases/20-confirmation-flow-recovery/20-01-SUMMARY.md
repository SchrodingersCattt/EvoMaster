---
phase: 20-confirmation-flow-recovery
plan: 01
subsystem: core
tags: [async, hooks, confirmation, asyncio-future, race-condition]

# Dependency graph
requires:
  - phase: 15-hook
    provides: "Original asyncio.Future-based ConfirmationHook contract and adapter path"
provides:
  - "Future-based ConfirmationHook restored on current async kernel path"
  - "Buffered early reply handling for emit-time and pre-request races"
  - "Expanded hook regression coverage for approval, cancel, timeout, and adapter contract"
affects: [20-confirmation-flow-recovery plan 02, worker-confirmation, stream-service]

# Tech tracking
tech-stack:
  added: []
  patterns: ["Future-based confirmation gate", "buffer early reply before pending future", "thread-safe reply delivery"]

key-files:
  created: []
  modified:
    - matmaster/hooks/confirmation.py
    - tests/matmaster/hooks/test_confirmation.py

key-decisions:
  - "Create the pending Future before emitting confirmation_request so emit-time replies are not dropped"
  - "Buffer replies that arrive before pre_tool_call registers a pending Future, because Worker-side Redis replies may race ahead of the hook"
  - "Keep resolve/cancel as the public cross-thread API so stream_service adapter and service-layer bridge stay aligned"

patterns-established:
  - "ConfirmationHook owns a tiny locked state machine: pending Future plus one buffered early reply"
  - "Hook regression tests explicitly cover both emit-time and pre-request reply races"

requirements-completed: []

# Metrics
duration: 25min
completed: 2026-03-30
---

# Phase 20 Plan 01: ConfirmationHook Recovery Summary

**Future-based ConfirmationHook restored on top of the Phase 21 baseline, with buffered early-reply handling so fast Worker replies no longer disappear before the hook starts waiting**

## Performance

- **Duration:** 25 min
- **Started:** 2026-03-29T16:05:00Z
- **Completed:** 2026-03-30T16:30:00Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Restored `ConfirmationHook` to the asyncio.Future waiting model expected by the async kernel
- Added race coverage for replies that arrive during `emit()` and before `pre_tool_call()` registers a pending Future
- Preserved the `ConfirmationHookAdapter` contract so existing reply producers still target `resolve()` and `cancel()`

## Task Commits

Atomic task commits have not been created yet.

- The user asked to align Phase 20 on the current Phase 21 base first
- This summary records the verified working-tree state and rationale
- Commit hashes can be backfilled later if you want to checkpoint the aligned state

## Files Created/Modified
- `matmaster/hooks/confirmation.py` - Restored async confirmation gate and buffered early replies that arrive before the waiter is active
- `tests/matmaster/hooks/test_confirmation.py` - Added async regression tests for emit-time race, pre-request race, cancel, timeout, non-gated pass-through, and adapter contract

## Decisions Made
- `ConfirmationHook` now treats early replies as valid input to the next pending confirmation instead of silently dropping them
- Race protection stays inside the hook rather than inside the service layer, so every caller benefits from the same semantics
- Requirement closure is deferred to plan 02 because the hook recovery alone does not reconnect the Worker reply queue

## Deviations from Plan

One necessary hardening step was added beyond the original Phase 15 recovery shape.

- The hook now buffers replies that arrive before the pending Future exists
- This was required by Phase 20 verification because the Worker-side Redis bridge can legally receive a reply before `pre_tool_call()` starts waiting

## Issues Encountered

- The first 20-02 integration verification exposed a deeper race: replies could arrive before `ConfirmationHook` had a pending Future
- Resolved by adding unit regressions and buffering early replies inside the hook

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- The hook contract is ready for service-layer reattachment in 20-02
- `resolve()` and `cancel()` are now safe for both bridge-thread and adapter-driven delivery paths
- Hook-level regressions give fast feedback for any future confirmation flow refactor
- This recovery preserves Phase 21's `execute_bash` async subprocess direction instead of rolling it back

---
*Phase: 20-confirmation-flow-recovery*
*Completed: 2026-03-30*
