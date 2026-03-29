---
phase: 18-exp
plan: 02
subsystem: core
tags: [asyncio, spawn, subagent, exp-run]

# Dependency graph
requires:
  - phase: 18-exp-plan-01
    provides: "Exp.assemble/build_runtime/run/_run_cleanup_callbacks all async def"
provides:
  - "async spawn_fn closure via child_exp.run() (no bridge loop)"
  - "SpawnTool.execute() native async override bypassing to_thread"
  - "Exp.run() source_override and spawn_id params for spawn chain"
affects: [19-service-layer]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "async spawn_fn reuses Exp.run() full lifecycle instead of manual build_runtime + kernel.run"
    - "SpawnTool overrides execute() directly for native async, _execute() retained as ABC stub"

key-files:
  created: []
  modified:
    - "matmaster/core/exp.py"
    - "matmaster/tools/builtin/spawn_tool.py"
    - "tests/matmaster/tools/test_spawn_tool.py"
    - "tests/matmaster/integration/test_subagent_spawn.py"
    - "tests/matmaster/core/test_exp.py"
    - "tests/matmaster/core/test_guard_injection.py"
    - "tests/matmaster/devshell/test_compaction_via_devshell.py"

key-decisions:
  - "spawn_fn calls child_exp.run() instead of manual build_runtime + kernel.run + cleanup -- reuses full Exp lifecycle"
  - "SpawnTool overrides execute() directly rather than using to_thread(_execute) pattern -- spawn_fn is async, no thread needed"
  - "Exp.run() gains source_override and spawn_id optional params transparently forwarded to build_runtime"

patterns-established:
  - "async spawn_fn closure: child_exp.run() encapsulates entire child agent lifecycle"
  - "BuiltinTool async override: subclass can override execute() for native async when to_thread pattern is inappropriate"

requirements-completed: [EXPL-04]

# Metrics
duration: 10min
completed: 2026-03-29
---

# Phase 18 Plan 02: SubAgent Spawn Async Chain Summary

**spawn_fn converted to async closure calling child_exp.run() for full lifecycle reuse, SpawnTool.execute() overridden for native async, bridge loop fully removed from exp.py**

## Performance

- **Duration:** 10 min
- **Started:** 2026-03-29T12:34:26Z
- **Completed:** 2026-03-29T12:45:21Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments
- spawn_fn in _make_spawn_fn converted from sync (with bridge loop) to async def
- spawn_fn now calls `await child_exp.run()` instead of manual build_runtime + kernel.run + cleanup
- Bridge loop (asyncio.new_event_loop) fully removed from exp.py -- zero bridge loops remain
- Exp.run() extended with `source_override` and `spawn_id` optional params for spawn chain
- SpawnTool.execute() overrides BuiltinTool.execute() for native async (bypasses to_thread)
- SpawnTool._execute() retained as ABC stub (raises NotImplementedError)
- Full error contract preserved: recursion guard, parameter validation, exception catch with Error: prefix
- All 23 spawn tests pass (13 unit + 10 integration)
- Full regression: 1057 passed, 3 skipped, 0 failed

## Task Commits

Each task was committed atomically:

1. **Task 1: spawn_fn async closure via Exp.run() + SpawnTool execute() override** - `c2e7893` (feat)
2. **Task 2: spawn tests AsyncMock migration + fix missed async test calls** - `96e14a6` (test)

## Files Created/Modified
- `matmaster/core/exp.py` - _make_spawn_fn returns async spawn_fn using child_exp.run(), Exp.run() gains source_override/spawn_id params, asyncio import removed
- `matmaster/tools/builtin/spawn_tool.py` - async execute() override with full error contract, _execute() stub, ToolResult import
- `tests/matmaster/tools/test_spawn_tool.py` - all spawn_fn Mock -> AsyncMock
- `tests/matmaster/integration/test_subagent_spawn.py` - tests mock Exp.run instead of build_runtime, await spawn_fn calls, unused imports cleaned
- `tests/matmaster/core/test_exp.py` - build_runtime assertions updated for source_override/spawn_id params
- `tests/matmaster/core/test_guard_injection.py` - fixed sync call to async Exp.assemble()
- `tests/matmaster/devshell/test_compaction_via_devshell.py` - fixed sync call to async Exp.assemble()

## Decisions Made
- spawn_fn calls child_exp.run() (not manual build_runtime + kernel.run) to reuse full Exp lifecycle including try/finally cleanup guarantee
- SpawnTool overrides execute() directly for native async because spawn_fn is async and to_thread pattern would be incorrect
- Exp.run() gains source_override/spawn_id params (default None) forwarded to build_runtime -- transparent to existing callers

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] test_exp.py build_runtime assertion mismatch**
- **Found during:** Task 2
- **Issue:** Exp.run() now passes source_override=None, spawn_id=None to build_runtime, but existing tests asserted only 4 kwargs
- **Fix:** Updated 2 assertions in test_exp.py to include source_override=None, spawn_id=None
- **Files modified:** tests/matmaster/core/test_exp.py
- **Committed in:** 96e14a6 (Task 2 commit)

**2. [Rule 1 - Bug] test_guard_injection.py sync call to async Exp.assemble()**
- **Found during:** Task 2 (full regression)
- **Issue:** test_guards_injected_via_assemble was sync def calling async Exp.assemble() without await (missed by Plan 01)
- **Fix:** Changed to async def + await exp.assemble(ctx)
- **Files modified:** tests/matmaster/core/test_guard_injection.py
- **Committed in:** 96e14a6 (Task 2 commit)

**3. [Rule 1 - Bug] test_compaction_via_devshell.py sync call to async Exp.assemble()**
- **Found during:** Task 2 (full regression)
- **Issue:** test_exp_assemble_compaction_disabled was sync def calling async Exp.assemble() without await (missed by Plan 01)
- **Fix:** Changed to async def + await exp.assemble(ctx)
- **Files modified:** tests/matmaster/devshell/test_compaction_via_devshell.py
- **Committed in:** 96e14a6 (Task 2 commit)

---

**Total deviations:** 3 auto-fixed (3 bugs)
**Impact on plan:** No scope creep. Deviations 2-3 were pre-existing missed async migrations from Plan 01.

## Issues Encountered
- Worktree was based on `test` branch, missing Phase 12-17 async changes from `refactor/async-matmaster`. Resolved by merging `refactor/async-matmaster` into the worktree before starting implementation (same approach as Plan 01).

## User Setup Required
None - no external service configuration required.

## Known Stubs
None - no placeholder data or unresolved stubs in modified files.

## Next Phase Readiness
- Exp layer fully async: assemble, build_runtime, run, cleanup, spawn_fn all async
- Zero bridge loops remain in exp.py
- Ready for Phase 19 service layer restructure

## Self-Check: PASSED

- All 7 modified source/test files exist on disk
- Both task commits (c2e7893, 96e14a6) found in git history
- SUMMARY.md created at expected path
- No stubs found in modified files

---
*Phase: 18-exp*
*Completed: 2026-03-29*
