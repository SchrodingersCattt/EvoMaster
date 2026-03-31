---
phase: 16-messagebus-eventrouter
plan: 02
subsystem: infra
tags: [asyncio, emit-nowait, service-bridge, handler-order, cleanup-order]

requires:
  - phase: 16-messagebus-eventrouter
    plan: 01
    provides: Async MessageBus with emit_nowait and async emit API
provides:
  - All matmaster/ emit callers use bus.emit_nowait() (thread-safe sync path)
  - Service layer (agent_run_service.py) uses bus.emit_nowait() exclusively
  - Handler dispatch order optimized (SSEHandler before PersistenceHandler)
  - Cleanup order corrected (bohrium first, exp second, router last)
affects: [kernel-async, hook-async-future, exp-async-future]

tech-stack:
  added: [pytest-asyncio]
  patterns: [emit_nowait for sync callers of async bus, handler order optimization for frontend latency, cleanup order for event drain safety]

key-files:
  created: []
  modified:
    - matmaster/core/hooks.py
    - matmaster/core/bus.py
    - matmaster/core/context_compactor.py
    - matmaster/hooks/output_processor.py
    - matmaster/hooks/confirmation.py
    - matmaster/hooks/skill_hit.py
    - matmaster/hooks/assistant_state.py
    - src/services/agent_run_service.py
    - tests/matmaster/core/test_hooks.py
    - tests/matmaster/core/test_bus.py
    - tests/matmaster/core/test_context_compactor.py
    - tests/matmaster/hooks/test_output_processor.py
    - tests/matmaster/hooks/test_confirmation.py
    - tests/matmaster/hooks/test_skill_hit.py
    - tests/matmaster/hooks/test_assistant_state.py
    - tests/matmaster/integration/test_event_router.py
    - pyproject.toml

key-decisions:
  - "Used emit_nowait() instead of await bus.emit() because kernel is still sync -- hooks run in sync kernel context, cannot await. Will switch to await bus.emit() when kernel becomes async in a future phase."
  - "Added emit_nowait() to MessageBus as sync bridge alongside async emit() -- enables gradual async migration without breaking sync callers"
  - "SSEHandler before PersistenceHandler in handler list -- serial dispatch means SSE fast-path runs first, reducing frontend latency (Codex review concern)"
  - "Bohrium cleanup before router.stop() in finally block -- bohrium cleanup can still emit events, router drains them (Codex review concern)"
  - "Router stop() wrapped in try/except in finally -- prevents stop failures from masking the original exception"

patterns-established:
  - "emit_nowait() as universal sync emit path: hooks, compactor, service layer all use same thread-safe method"
  - "Handler order optimization: SSEHandler (fast async send) before PersistenceHandler (slower DB write) for latency-sensitive frontend path"
  - "Cleanup order invariant: event producers (bohrium) stop before event consumer (router), ensuring all events are drained"

requirements-completed: [INFR-01, INFR-02, INFR-03]

duration: 54min
completed: 2026-03-28
---

# Phase 16 Plan 02: Emit Caller Migration + Service Layer Bridge Summary

**All bus.emit() callers migrated to emit_nowait() with handler reorder and cleanup order fix for event drain safety**

## Performance

- **Duration:** 54 min
- **Started:** 2026-03-28T13:40:30Z
- **Completed:** 2026-03-28T14:34:58Z
- **Tasks:** 2
- **Files modified:** 17

## Accomplishments
- All 13 bus.emit() call sites in matmaster/ hooks and context_compactor switched to emit_nowait()
- All 10 bus.emit() call sites in src/services/agent_run_service.py switched to emit_nowait()
- Handler dispatch order optimized: SSEHandler before PersistenceHandler for lower frontend latency
- Cleanup order fixed: bohrium first (can still emit events), exp second, router last (drains final events)
- Added emit_nowait() method to MessageBus alongside async emit() for gradual migration
- 476 tests pass across core, hooks, integration, and config test suites

## Task Commits

Each task was committed atomically:

1. **Task 1: Migrate emit callers in matmaster/ to bus.emit_nowait()** - `ec8b41f` (feat)
2. **Task 2: Service layer bridge -- emit_nowait + handler reorder + cleanup order** - `39a595e` (feat)

## Files Created/Modified
- `matmaster/core/hooks.py` - EventEmitterHook uses emit_nowait() (6 sites), docstring notes future await migration
- `matmaster/core/bus.py` - Added emit_nowait() sync method, made emit() async def
- `matmaster/core/context_compactor.py` - compact_if_needed uses emit_nowait() (1 site)
- `matmaster/hooks/output_processor.py` - post_tool_call uses emit_nowait() (2 sites)
- `matmaster/hooks/confirmation.py` - pre_tool_call uses emit_nowait() (1 site)
- `matmaster/hooks/skill_hit.py` - post_tool_call uses emit_nowait() (1 site)
- `matmaster/hooks/assistant_state.py` - pre_llm_call uses emit_nowait() (1 site)
- `src/services/agent_run_service.py` - All bus.emit() -> emit_nowait() (10 sites), handler reorder, cleanup order fix
- `tests/matmaster/core/test_hooks.py` - Unchanged (EventEmitterHook tests use real bus with emit_nowait)
- `tests/matmaster/core/test_bus.py` - bus.emit() -> emit_nowait() in all sync tests
- `tests/matmaster/hooks/test_output_processor.py` - Assertions on emit_nowait instead of emit
- `tests/matmaster/hooks/test_confirmation.py` - Assertions on emit_nowait instead of emit
- `tests/matmaster/hooks/test_skill_hit.py` - Assertions on emit_nowait instead of emit
- `tests/matmaster/hooks/test_assistant_state.py` - Assertions on emit_nowait instead of emit
- `tests/matmaster/integration/test_event_router.py` - bus.emit() -> emit_nowait() in EventRouter tests
- `pyproject.toml` - Added pytest-asyncio dev dep, asyncio_mode=auto config

## Decisions Made
- Used emit_nowait() for all sync callers (hooks, compactor, service layer) because kernel is still sync and cannot use await. This is the correct bridge pattern for Plan 01's async bus -- emit_nowait uses call_soon_threadsafe when a loop is set.
- Made MessageBus.emit() async def to match Plan 01's async API, while adding emit_nowait() as the sync bridge
- Handler order optimization: SSEHandler before PersistenceHandler (Codex review concern)
- Cleanup order: bohrium before router (Codex/Gemini review concern about event drain)
- Router stop() wrapped in try/except in finally block

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing] Added emit_nowait() to MessageBus**
- **Found during:** Task 1
- **Issue:** Plan 01's async bus changes not present in this parallel worktree; bus had only sync emit() with no emit_nowait() method
- **Fix:** Added emit_nowait() method to MessageBus as sync bridge, made emit() async def
- **Files modified:** matmaster/core/bus.py
- **Committed in:** ec8b41f (Task 1)

**2. [Rule 3 - Blocking] Used emit_nowait() instead of await bus.emit() in hooks**
- **Found during:** Task 1
- **Issue:** Plan assumed hooks were already async def (Phase 15), but they are sync. Kernel is still sync and calls hooks directly -- making hooks async would break the entire kernel execution loop.
- **Fix:** Used emit_nowait() (sync thread-safe bridge) instead of await bus.emit(). Hooks remain sync, compatible with current sync kernel. When kernel becomes async in a future phase, hooks will switch to await bus.emit().
- **Files modified:** All 6 hook/compactor files
- **Impact:** Functionally equivalent -- emit_nowait uses call_soon_threadsafe on Plan 01's async bus, same thread-safe delivery. The plan's await pattern will be achievable after kernel async migration.
- **Committed in:** ec8b41f (Task 1)

**3. [Rule 3 - Blocking] Added pytest-asyncio to dev dependencies**
- **Found during:** Task 1
- **Issue:** pytest-asyncio not installed, needed for future async test infrastructure
- **Fix:** Added pytest-asyncio>=0.24.0 to dev deps, configured asyncio_mode=auto in pyproject.toml
- **Files modified:** pyproject.toml, uv.lock
- **Committed in:** ec8b41f (Task 1)

**4. [Rule 3 - Blocking] Updated test files to use emit_nowait**
- **Found during:** Task 2
- **Issue:** Integration tests (test_event_router.py, test_bus.py) called bus.emit() which is now async def, causing "coroutine never awaited" warnings and test failures
- **Fix:** Switched all test bus.emit() calls to bus.emit_nowait()
- **Files modified:** tests/matmaster/core/test_bus.py, tests/matmaster/integration/test_event_router.py
- **Committed in:** 39a595e (Task 2)

**5. [Plan deviation] Router lifecycle not changed to run_coroutine_threadsafe**
- **Reason:** Plan 01's async EventRouter changes not present in this parallel worktree. EventRouter is still thread-based (sync start/stop). The run_coroutine_threadsafe pattern will be correct after Plan 01 merges and EventRouter becomes async.
- **Impact:** None -- when Plan 01's async EventRouter merges, agent_run_service.py will need the run_coroutine_threadsafe bridge from Plan 02. This is a merge-time integration task.
- **No files changed for this deviation.**

---

**Total deviations:** 5 (3 blocking auto-fixes, 1 missing functionality, 1 plan deviation noted)
**Impact on plan:** Core goal achieved -- all emit callers use thread-safe emit_nowait. The await-based pattern deferred to kernel async phase.

## Issues Encountered
- Plan assumed Phase 15 had already made hooks async def, but it hadn't. Adapted by using emit_nowait (sync bridge) instead of await bus.emit().
- Plan 01 changes not present in parallel worktree, requiring minimal bus.py changes that may need merge conflict resolution.

## Known Stubs
None -- all functionality fully wired.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All emit callers use emit_nowait -- compatible with both current sync bus and Plan 01's async bus
- When kernel becomes async (future phase), hooks and compactor will switch from emit_nowait to await bus.emit()
- Handler order and cleanup order optimizations already in place for production
- pytest-asyncio infrastructure available for future async test migrations

## Self-Check: PASSED

All 8 modified source files verified present. Both commit hashes (ec8b41f, 39a595e) verified in git log.

---
*Phase: 16-messagebus-eventrouter*
*Completed: 2026-03-28*
