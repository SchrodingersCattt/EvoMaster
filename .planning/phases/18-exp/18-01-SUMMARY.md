---
phase: 18-exp
plan: 01
subsystem: core
tags: [asyncio, exp, lifecycle, cleanup, bridge-loop]

# Dependency graph
requires:
  - phase: 17-agentkernel
    provides: "AgentKernel.run() async def, bridge loops in Exp.run() and spawn_fn"
provides:
  - "Exp.assemble/build_runtime/run/_run_cleanup_callbacks all async def"
  - "AgentRuntime.cleanup type Callable[[], Any] supporting async callbacks"
  - "Service/DevShell bridge loops covering build_runtime + kernel.run + cleanup"
  - "iscoroutinefunction + isawaitable dual detection for cleanup callbacks"
affects: [18-exp-plan-02, 19-service-layer]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "async cleanup dispatch with iscoroutinefunction + isawaitable fallback"
    - "try/finally from build_runtime for partial build failure cleanup"
    - "bridge loop extension pattern: single _loop covers build_runtime + kernel.run + cleanup"

key-files:
  created: []
  modified:
    - "matmaster/core/exp.py"
    - "matmaster/types/runtime.py"
    - "src/services/agent_run_service.py"
    - "matmaster/devshell/runner.py"
    - "matmaster/devshell/repl.py"
    - "tests/matmaster/core/test_exp.py"
    - "tests/matmaster/integration/test_e2e_minimal.py"
    - "tests/matmaster/integration/test_e2e_mat_master.py"
    - "tests/matmaster/integration/test_pipeline_alignment.py"
    - "tests/matmaster/integration/test_upstream_scenarios.py"
    - "tests/matmaster/devshell/test_repl.py"
    - "tests/matmaster/integration/test_bohrium_execution_contract.py"

key-decisions:
  - "run() try/finally starts before build_runtime to cover partial build failures"
  - "cleanup callback dispatch uses iscoroutinefunction first, isawaitable fallback for wrapped/partial callables"
  - "AgentRuntime.cleanup typed as Callable[[], Any] for sync/async compatibility"
  - "Service layer cleanup moved inside bridge loop before _loop.close(), removed from outer finally"

patterns-established:
  - "async cleanup dispatch: iscoroutinefunction check -> isawaitable fallback"
  - "bridge loop extension: _loop covers build_runtime + kernel.run + cleanup in single try/finally"

requirements-completed: [EXPL-01, EXPL-02, EXPL-03]

# Metrics
duration: 12min
completed: 2026-03-29
---

# Phase 18 Plan 01: Exp Lifecycle Async Summary

**Exp 4 methods (assemble/build_runtime/run/_run_cleanup_callbacks) converted to async def with bridge loop removed from run(), cleanup upgraded to dual sync/async dispatch**

## Performance

- **Duration:** 12 min
- **Started:** 2026-03-29T12:15:30Z
- **Completed:** 2026-03-29T12:28:27Z
- **Tasks:** 2
- **Files modified:** 12

## Accomplishments
- All 4 Exp lifecycle methods converted to async def (assemble, build_runtime, run, _run_cleanup_callbacks)
- Bridge loop removed from Exp.run() -- now directly awaits build_runtime and kernel.run
- Cleanup callback mechanism upgraded with iscoroutinefunction + isawaitable dual detection for future async callbacks
- Service layer and DevShell bridge loops extended to cover build_runtime + kernel.run + cleanup in single event loop
- run() try/finally now starts before build_runtime, guaranteeing cleanup even on partial build failures
- All 89 affected tests pass (44 test_exp.py + 21 integration + 24 devshell/bohrium)

## Task Commits

Each task was committed atomically:

1. **Task 1: Exp core 4 methods async + AgentRuntime type update + service/DevShell bridge** - `9c19136` (feat)
2. **Task 2: test_exp.py + all affected tests async migration** - `02a0bcc` (test)

## Files Created/Modified
- `matmaster/core/exp.py` - 4 methods async, bridge loop removed from run(), cleanup upgraded
- `matmaster/types/runtime.py` - AgentRuntime.cleanup type changed to Callable[[], Any]
- `src/services/agent_run_service.py` - Bridge loop extended for build_runtime, cleanup inside _loop
- `matmaster/devshell/runner.py` - Bridge loop extended for build_runtime + cleanup
- `matmaster/devshell/repl.py` - _show_tools bridge loop for async build_runtime + cleanup
- `tests/matmaster/core/test_exp.py` - 44 tests migrated to async def with AsyncMock
- `tests/matmaster/integration/test_e2e_minimal.py` - await exp.build_runtime
- `tests/matmaster/integration/test_e2e_mat_master.py` - await exp.build_runtime (3 sites)
- `tests/matmaster/integration/test_pipeline_alignment.py` - await exp.build_runtime
- `tests/matmaster/integration/test_upstream_scenarios.py` - await exp.build_runtime (2 sites)
- `tests/matmaster/devshell/test_repl.py` - AsyncMock for build_runtime and _run_cleanup_callbacks
- `tests/matmaster/integration/test_bohrium_execution_contract.py` - AsyncMock for build_runtime, kernel.run, _run_cleanup_callbacks

## Decisions Made
- run() try/finally starts before build_runtime (not after) to cover partial build failures where some cleanup callbacks have already been registered
- Service layer exp cleanup moved from outer finally block into bridge loop's finally block (before _loop.close()), since _run_cleanup_callbacks is now async and requires the event loop
- AgentRuntime.cleanup typed as Callable[[], Any] rather than Union type -- simpler, and callers that need await can inspect the result

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Service layer cleanup placement adapted to actual code structure**
- **Found during:** Task 1
- **Issue:** Plan assumed cleanup was in the same try/finally as kernel.run bridge loop. Actual code had cleanup in a separate outer finally block with ordered cleanup (Bohrium first, Exp second, Router last)
- **Fix:** Moved Exp cleanup into bridge loop's finally (before _loop.close()), replaced outer finally Exp cleanup with a comment noting it's handled inside the bridge
- **Files modified:** src/services/agent_run_service.py
- **Verification:** All integration tests pass including test_bohrium_execution_contract
- **Committed in:** 9c19136 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Adaptation to actual code structure. No scope creep.

## Issues Encountered
- Worktree was based on `test` branch, missing Phase 12-17 async changes from `refactor/async-matmaster`. Resolved by merging `refactor/async-matmaster` into the worktree before starting implementation.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Exp lifecycle fully async, ready for Plan 02 (SubAgent spawn async chain)
- _make_spawn_fn still has bridge loop (asyncio.new_event_loop) -- Plan 02 will convert it to async
- Service layer bridge pattern established and tested, ready for Phase 19 service layer restructure

## Self-Check: PASSED

- All 12 modified source/test files exist on disk
- Both task commits (9c19136, 02a0bcc) found in git history
- SUMMARY.md created at expected path
- No stubs found in modified files

---
*Phase: 18-exp*
*Completed: 2026-03-29*
