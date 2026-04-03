---
phase: 36-debus-scheduling
plan: 01
subsystem: integration
tags: [asyncio, fanout, event-dispatch, sse, persistence, bohrium, thread-safety]

# Dependency graph
requires:
  - phase: 34-generator-integration
    provides: Exp.run_stream() generator pipeline, SSEHandler/PersistenceHandler/WorkspaceHandler
provides:
  - RunEventFanout per-run async dispatch owner
  - EventHandler Protocol in fanout module
  - Fanout-backed run_agent_stream() with SSE-first dispatch
  - Thread-safe Bohrium event_sink bridge replacing bus
affects: [36-02, 36-03, 36-04]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-run RunEventFanout owner: SSE awaited first, persistence as background asyncio.Task, drain_and_close() lifecycle"
    - "Thread-safe event_sink: loop.call_soon_threadsafe + asyncio.create_task for Bohrium worker-thread callbacks"
    - "Strong-reference set[Task] for pending persistence tasks with add_done_callback discard"

key-files:
  created:
    - matmaster/integration/fanout.py
    - tests/matmaster/integration/test_event_fanout.py
  modified:
    - src/services/agent_run_service.py
    - src/services/agent_run_bohrium.py
    - tests/matmaster/services/test_agent_run_stream.py
    - tests/matmaster/test_bohrium_setup_injection.py

key-decisions:
  - "EventHandler Protocol moved to fanout.py for post-EventRouter-deletion survival"
  - "Persistence dispatch uses asyncio.create_task with strong-reference set (no TaskGroup for Python 3.10 compat)"
  - "BohriumSetupService takes event_sink: Callable instead of bus: MessageBus"

patterns-established:
  - "RunEventFanout dispatch order: SSE await -> extra handlers await -> persistence background task"
  - "drain_and_close() lifecycle: gather pending tasks -> close all handlers (sync/async)"
  - "_dispatch_from_thread closure: loop.call_soon_threadsafe(lambda: create_task(fanout.dispatch(event)))"

requirements-completed: [DBUS-01, DBUS-02]

# Metrics
duration: 9min
completed: 2026-04-03
---

# Phase 36 Plan 01: RunEventFanout Infrastructure Summary

**Per-run async fanout owner with SSE-first dispatch replacing EventRouter transport, plus Bohrium thread-safe event_sink bridge**

## Performance

- **Duration:** 9 min
- **Started:** 2026-04-03T08:34:11Z
- **Completed:** 2026-04-03T08:43:00Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- RunEventFanout: per-run async dispatch owner with SSE-first, background persistence, and handler close lifecycle
- run_agent_stream() fully migrated from bus.emit_nowait() to await fanout.dispatch()
- BohriumSetupService._make_event_bridge() maps callbacks to concrete BusEvent objects via event_sink
- 37 regression tests passing: 12 fanout + 7 workspace handler + 9 stream + 9 Bohrium

## Task Commits

Each task was committed atomically:

1. **Task 1: Introduce RunEventFanout and port router lifecycle coverage** - `f9ce360b` (feat)
2. **Task 2: Cut run_agent_stream and Bohrium callbacks over to fanout** - `a325166e` (feat)

## Files Created/Modified
- `matmaster/integration/fanout.py` - RunEventFanout owner: dispatch(), add_handler(), drain_and_close(), EventHandler Protocol
- `tests/matmaster/integration/test_event_fanout.py` - 12 tests: dispatch order, add_handler, error isolation, persistence drain, close
- `src/services/agent_run_service.py` - run_agent_stream() uses RunEventFanout, _emit_error_and_close_fanout(), thread-safe _dispatch_from_thread
- `src/services/agent_run_bohrium.py` - BohriumSetupService accepts event_sink, _make_event_bridge uses sink() instead of bus.emit_nowait()
- `tests/matmaster/services/test_agent_run_stream.py` - Rewritten to assert handler dispatch via fanout, worker-mode send_cb live path
- `tests/matmaster/test_bohrium_setup_injection.py` - Added event_sink constructor tests, bridge mapping tests (error/stream_closed/bohrium_node)

## Decisions Made
- EventHandler Protocol placed in fanout.py (not a separate types file) so it survives EventRouter deletion in Plan 02
- Used set[asyncio.Task] instead of TaskGroup for pending persistence tracking (Python 3.10 compat per pyproject.toml)
- BohriumSetupService takes event_sink: Callable[[BusEvent], None] replacing bus: MessageBus -- cleaner interface, no bus dependency
- Workspace handler registered via fanout.add_handler() (same timing as old router.add_handler())
- run_agent_stream() no longer creates MessageBus (fanout replaces the entire bus+router chain)
- ConfirmationHook section removed from run_agent_stream() (already dead code with _CONFIRM_TOOLS empty, per D-03)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed worktree-incompatible Path resolution in Bohrium location tests**
- **Found during:** Task 2
- **Issue:** TestBohriumSetupServiceLocation used `Path(__file__).parent.parent / "src"` which resolves incorrectly in git worktrees
- **Fix:** Added `_project_root()` helper that walks up to find directory containing both src/ and matmaster/
- **Files modified:** tests/matmaster/test_bohrium_setup_injection.py
- **Verification:** All 9 Bohrium tests pass
- **Committed in:** a325166e (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Pre-existing test fragility in worktree environments. No scope creep.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- RunEventFanout infrastructure ready for Plan 02 to delete MessageBus + EventRouter
- run_agent_stream() no longer depends on bus/router (can be deleted in Plan 02)
- run_agent() legacy path still uses bus/router (Plan 02 will delete it)
- Exp.run_stream() bus= parameter still accepted but unused (Plan 03 cleanup)

## Self-Check: PASSED

All 7 files verified present. Both commit hashes (f9ce360b, a325166e) found in git log.

---
*Phase: 36-debus-scheduling*
*Completed: 2026-04-03*
