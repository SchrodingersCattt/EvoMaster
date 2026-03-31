---
phase: 24-emit-nowait-tech-debt
plan: 01
subsystem: core
tags: [asyncio, messagebus, hooks, emit, tech-debt]

# Dependency graph
requires:
  - phase: 16-messagebus-eventrouter
    provides: MessageBus emit/emit_nowait dual API
  - phase: 17-agentkernel
    provides: Async kernel execution loop
provides:
  - "All matmaster/ emit calls use await bus.emit() (proper async path)"
  - "emit_nowait() preserved exclusively for src/ service layer cross-thread use"
  - "Clean docstrings with no stale sync-kernel references"
  - "stop_event typed as threading.Event in agent_run_service"
affects: [src-service-layer, future-async-phases]

# Tech tracking
tech-stack:
  added: []
  patterns: ["await bus.emit() as canonical emit path within event loop"]

key-files:
  created: []
  modified:
    - matmaster/core/hooks.py
    - matmaster/core/bus.py
    - matmaster/core/context_compactor.py
    - matmaster/hooks/assistant_state.py
    - matmaster/hooks/output_processor.py
    - matmaster/hooks/skill_hit.py
    - src/services/agent_run_service.py
    - tests/matmaster/hooks/test_output_processor.py
    - tests/matmaster/hooks/test_assistant_state.py
    - tests/matmaster/hooks/test_skill_hit.py

key-decisions:
  - "MagicMock(emit=AsyncMock()) pattern for testing async bus.emit() callers"

patterns-established:
  - "await bus.emit() for all matmaster/ code running inside the event loop"
  - "emit_nowait() reserved for src/ service layer cross-thread bridge only"

requirements-completed: [HOOK-03]

# Metrics
duration: 7min
completed: 2026-03-30
---

# Phase 24 Plan 01: emit_nowait Tech Debt Closure Summary

**Migrated 12 emit_nowait() calls to await bus.emit() across hooks and compactor, closing Phase 16 async deviation**

## Performance

- **Duration:** 7 min
- **Started:** 2026-03-29T19:15:25Z
- **Completed:** 2026-03-29T19:22:54Z
- **Tasks:** 2
- **Files modified:** 10

## Accomplishments
- Migrated all 12 emit_nowait() call sites in matmaster/ production code to await bus.emit()
- Removed 4 stale docstring lines referencing "sync kernel context"
- Updated MessageBus class docstring to document emit() as canonical async path
- Fixed stop_event type annotation from Any to threading.Event in agent_run_service.py
- Updated 17 mock assertions across 3 test files (10 positive + 7 negative)
- Full test suite green: 1195 tests pass, 0 failures

## Task Commits

Each task was committed atomically:

1. **Task 1: Migrate 12 emit_nowait calls + clean stale comments + update bus docstring + fix type annotation** - `d9bff32` (feat)
2. **Task 2: Update 3 mock-based test files emit_nowait assertions to emit** - `0245081` (test)

## Files Created/Modified
- `matmaster/core/hooks.py` - EventEmitterHook: 6 emit_nowait -> await bus.emit()
- `matmaster/core/bus.py` - Updated class docstring to reflect emit() as primary path
- `matmaster/core/context_compactor.py` - 2 emit_nowait -> await bus.emit() (None guard preserved)
- `matmaster/hooks/assistant_state.py` - 1 emit_nowait -> await bus.emit(), stale comment removed
- `matmaster/hooks/output_processor.py` - 2 emit_nowait -> await bus.emit(), stale comment removed
- `matmaster/hooks/skill_hit.py` - 1 emit_nowait -> await bus.emit(), stale comment removed
- `src/services/agent_run_service.py` - stop_event: Any -> threading.Event
- `tests/matmaster/hooks/test_output_processor.py` - 6 assertions updated, AsyncMock for bus.emit
- `tests/matmaster/hooks/test_assistant_state.py` - 6 assertions updated, AsyncMock for bus.emit
- `tests/matmaster/hooks/test_skill_hit.py` - 5 assertions updated, AsyncMock for bus.emit

## Decisions Made
- Used MagicMock(emit=AsyncMock()) pattern to make bus mock await-compatible without replacing all MagicMock() with AsyncMock() globally. This keeps other bus attributes (like constructor args) as regular mocks while making only emit() awaitable.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added AsyncMock for bus.emit in test mocks**
- **Found during:** Task 2 (test assertion updates)
- **Issue:** Plan specified replacing `bus.emit_nowait` with `bus.emit` in assertions, but MagicMock().emit returns a non-awaitable MagicMock. Since production code now does `await self._bus.emit(...)`, the mock must return an awaitable.
- **Fix:** Added `AsyncMock` import and changed `bus = MagicMock()` to `bus = MagicMock(emit=AsyncMock())` in all 3 test files
- **Files modified:** tests/matmaster/hooks/test_output_processor.py, test_assistant_state.py, test_skill_hit.py
- **Verification:** All 12 hook tests pass
- **Committed in:** 0245081 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Necessary fix -- without AsyncMock, all hook tests would fail with TypeError. No scope creep.

## Issues Encountered
None - pre-existing collection errors in tests/test_chat_session_list.py and tests/test_openapi_chat_docs.py (read-only /data filesystem) are unrelated to this plan's changes.

## User Setup Required
None - no external service configuration required.

## Known Stubs
None - no stubs introduced or present in modified files.

## Next Phase Readiness
- emit_nowait tech debt fully closed
- matmaster/ production code is now fully async-consistent for MessageBus usage
- emit_nowait() remains available in bus.py for src/ service layer cross-thread callers

---
*Phase: 24-emit-nowait-tech-debt*
*Completed: 2026-03-30*
