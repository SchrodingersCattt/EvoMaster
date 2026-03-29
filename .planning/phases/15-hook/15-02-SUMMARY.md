---
phase: 15-hook
plan: 02
subsystem: core
tags: [async, hooks, confirmation, asyncio-future, thread-safe, adapter-pattern]

# Dependency graph
requires:
  - phase: 15-hook-01
    provides: Async Hook Protocol + BaseHook, _sync_call_async bridge, pytest-asyncio
provides:
  - asyncio.Future-based ConfirmationHook (non-blocking confirmation wait)
  - Thread-safe resolve/cancel with atomic swap pattern
  - Kernel loop injection via duck-typed set_loop
  - ConfirmationHookAdapter bridging ReplyQueueLike to hook.resolve/cancel
  - ReplyQueueLike preserved in agent_run_service.py (deprecated, import chain intact)
affects: [phase-16-messagebus, phase-17-kernel-async, stream-service-migration]

# Tech tracking
tech-stack:
  added: []
  patterns: [asyncio.Future confirmation, atomic swap race prevention, adapter bridge pattern, duck-typed loop injection]

key-files:
  created: []
  modified:
    - matmaster/hooks/confirmation.py
    - matmaster/core/agent.py
    - src/services/agent_run_service.py
    - src/services/stream_service.py
    - tests/matmaster/hooks/test_confirmation.py
    - tests/matmaster/integration/test_upstream_scenarios.py

key-decisions:
  - "ConfirmationHook uses asyncio.Future + wait_for instead of queue.Queue.get for non-blocking confirmation"
  - "resolve/cancel use atomic swap pattern: read _pending_future then immediately None it to prevent race"
  - "Kernel injects bridge loop via duck-typed hasattr(hook, set_loop) -- no import dependency on ConfirmationHook"
  - "ReplyQueueLike preserved in agent_run_service.py with deprecation notice for stream_service import chain"
  - "ConfirmationHookAdapter in stream_service.py bridges existing put_content/put_cancel API to hook.resolve/cancel"
  - "timeout_sec changed from int to float for sub-second test timeouts, cast to int for event emission"

patterns-established:
  - "Atomic swap pattern for thread-safe Future resolution: grab reference + clear field before checking done()"
  - "ConfirmationHookAdapter: bridge legacy ReplyQueueLike callers to new async hook API"
  - "Duck-typed loop injection: Kernel checks hasattr(hook, set_loop) to avoid coupling to specific hook types"

requirements-completed: [HOOK-02]

# Metrics
duration: 12min
completed: 2026-03-27
---

# Phase 15 Plan 02: ConfirmationHook Async Summary

**ConfirmationHook refactored from queue.Queue blocking to asyncio.Future + wait_for with atomic swap resolve/cancel and ConfirmationHookAdapter bridging the full confirmation path**

## Performance

- **Duration:** 12 min
- **Started:** 2026-03-27T15:13:41Z
- **Completed:** 2026-03-27T15:25:44Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- ConfirmationHook completely rewritten: asyncio.Future + wait_for replaces queue.Queue.get blocking
- Thread-safe resolve/cancel with atomic swap pattern prevents race conditions (Review P2 fix)
- ReplyQueueLike removed from hooks/confirmation.py, preserved with deprecation in agent_run_service.py (Review P1-1 fix)
- ConfirmationHookAdapter in stream_service.py bridges full confirmation path: chat_api -> adapter -> hook (Review P1-2 fix)
- Kernel injects bridge loop via duck-typed set_loop (no import coupling)
- 98 hook-related tests pass including 13 new async confirmation tests

## Task Commits

Each task was committed atomically:

1. **Task 1: ConfirmationHook Future refactor + Kernel loop injection + resolve/cancel atomic swap** - `70521e2` (feat)
2. **Task 2: src/ layer confirmation path adapter + test rewrite** - `e74addb` (feat)

## Files Created/Modified
- `matmaster/hooks/confirmation.py` - Complete rewrite: asyncio.Future + wait_for, resolve/cancel with atomic swap, set_loop injection
- `matmaster/core/agent.py` - Loop injection: iterate hooks with hasattr(set_loop) check in _run_loop
- `src/services/agent_run_service.py` - ReplyQueueLike deprecated docstring, updated ConfirmationHook comment pattern
- `src/services/stream_service.py` - Added ConfirmationHookAdapter class, import ConfirmationHook
- `tests/matmaster/hooks/test_confirmation.py` - Full rewrite: 13 async tests (no-loop, with-loop, cross-thread, adapter)
- `tests/matmaster/integration/test_upstream_scenarios.py` - TestCrossPodReplyQueue rewritten for async Future model

## Decisions Made
- ConfirmationHook uses asyncio.Future + wait_for for non-blocking async wait (replaces queue.Queue.get blocking)
- resolve/cancel use atomic swap: read _pending_future and set None immediately to prevent second caller race
- Kernel uses duck-typed hasattr(hook, "set_loop") to avoid importing ConfirmationHook
- ReplyQueueLike kept in agent_run_service.py with deprecation notice (stream_service.py imports it)
- ConfirmationHookAdapter implements ReplyQueueLike interface forwarding to hook.resolve/cancel
- timeout_sec changed from int to float for sub-second test timeouts, cast to int when emitting ConfirmationRequestEvent

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] timeout_sec type mismatch with Pydantic int field**
- **Found during:** Task 2 (test rewrite)
- **Issue:** Test used timeout_sec=0.05 (float) but ConfirmationRequestEvent.timeout_seconds is int, causing Pydantic validation error
- **Fix:** Changed timeout_sec type from int to float, cast to int(self._timeout_sec) when emitting event
- **Files modified:** matmaster/hooks/confirmation.py
- **Verification:** All 13 confirmation tests pass including 0.05s timeout test
- **Committed in:** e74addb (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Minor type fix necessary for sub-second timeouts in tests. No scope creep.

## Issues Encountered
None -- all tests pass as expected.

## Known Stubs
None -- no stubs or placeholders introduced.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All Hook implementations (including ConfirmationHook) are now async
- Complete confirmation path wired: chat_api -> stream_service.ConfirmationHookAdapter -> hook.resolve/cancel
- ConfirmationHook currently disabled in agent_run_service.py (commented out), ready to re-enable when confirm_tools list is defined
- _bridge_loop pattern will be removed in Phase 17 when Kernel itself becomes async

## Self-Check: PASSED

All 7 files verified present. Both commit hashes (70521e2, e74addb) confirmed in git history.

---
*Phase: 15-hook*
*Completed: 2026-03-27*
