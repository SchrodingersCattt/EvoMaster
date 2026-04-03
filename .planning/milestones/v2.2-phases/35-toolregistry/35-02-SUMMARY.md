---
phase: 35-toolregistry
plan: 02
subsystem: tools
tags: [tool-runtime, stop-mode, cancel-strategy, tool-compiler]

# Dependency graph
requires:
  - phase: 33
    provides: "ToolBinding with state_mode/stop_mode Literal fields, FullToolRunner seven-step chain"
provides:
  - "BUILTIN_STOP_MODES mapping table for all 16 builtin tools"
  - "ToolCompiler populates state_mode/stop_mode per tool"
  - "FullToolRunner stop_mode-aware cancel strategy (cancellable/best_effort/non_cancellable)"
affects: [35-03, tool-scheduler, agent-kernel]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "BUILTIN_STOP_MODES parallel lookup table alongside BUILTIN_CLAIMS and BUILTIN_META"
    - "Cancel check moved after catalog lookup to access stop_mode metadata"

key-files:
  created: []
  modified:
    - matmaster/tools/tool_compiler.py
    - matmaster/core/tool_runner.py
    - tests/matmaster/tools/test_tool_compiler.py
    - tests/matmaster/core/test_tool_runner.py

key-decisions:
  - "Cancel check moved from step 1 (before catalog lookup) to step 1b (after catalog lookup) to access instance.tool_binding.stop_mode"
  - "BUILTIN_STOP_MODES uses same dict[str, tuple] pattern as BUILTIN_CLAIMS and BUILTIN_META for consistency"

patterns-established:
  - "BUILTIN_STOP_MODES: per-tool (state_mode, stop_mode) mapping, default (stateless, cancellable) for unknown tools"
  - "stop_mode-aware cancel: cancellable=immediate, best_effort=cancel-with-message, non_cancellable=skip-cancel"

requirements-completed: [CMIG-03]

# Metrics
duration: 4min
completed: 2026-04-03
---

# Phase 35 Plan 02: state_mode/stop_mode Enablement Summary

**BUILTIN_STOP_MODES mapping table + FullToolRunner stop_mode-aware cancel strategy for 16 builtin tools**

## Performance

- **Duration:** 4 min
- **Started:** 2026-04-03T04:27:34Z
- **Completed:** 2026-04-03T04:31:26Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Added BUILTIN_STOP_MODES mapping table covering all 16 builtin tools with per-tool (state_mode, stop_mode) tuples
- ToolCompiler.compile() now populates state_mode/stop_mode from the mapping, with (stateless, cancellable) fallback for unknown tools
- FullToolRunner cancel check is now stop_mode-aware: cancellable tools get immediate cancel, best_effort tools get cancel with message, non_cancellable tools (e.g. spawn) skip cancel and execute normally

## Task Commits

Each task was committed atomically:

1. **Task 1: ToolCompiler state_mode/stop_mode population** - `3cd48009` (feat)
2. **Task 2: FullToolRunner stop_mode-aware cancel strategy** - `0681af63` (feat)

_Both tasks followed TDD: RED (failing tests) -> GREEN (implementation) -> verify_

## Files Created/Modified
- `matmaster/tools/tool_compiler.py` - Added BUILTIN_STOP_MODES mapping table, wired into compile()
- `matmaster/core/tool_runner.py` - Restructured cancel check to be stop_mode-aware after catalog lookup
- `tests/matmaster/tools/test_tool_compiler.py` - 7 new tests for state_mode/stop_mode compilation
- `tests/matmaster/core/test_tool_runner.py` - 4 new tests for FullToolRunner stop_mode cancel behavior

## Decisions Made
- Cancel check moved from step 1 (before catalog) to step 1b (after catalog) because stop_mode lives on ToolBinding which is only available after catalog lookup
- BUILTIN_STOP_MODES uses same dict[str, tuple] pattern as existing BUILTIN_CLAIMS and BUILTIN_META for code consistency
- Unknown tools default to (stateless, cancellable) which preserves backward-compatible cancel-all behavior

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed ToolCatalog cache attribute name in test**
- **Found during:** Task 2 (TDD RED phase)
- **Issue:** Test helper used `catalog._cache` but actual attribute is `catalog._compiled_tools`
- **Fix:** Changed to `catalog._compiled_tools` to match ToolCatalog internals
- **Files modified:** tests/matmaster/core/test_tool_runner.py
- **Verification:** All 4 stop_mode tests pass
- **Committed in:** 0681af63 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Trivial test infrastructure fix. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Known Stubs
None - all functionality is fully wired.

## Next Phase Readiness
- stop_mode metadata is now available on every ToolInstance, ready for ToolScheduler consumption in Plan 03
- FullToolRunner properly handles all three cancel strategies

---
*Phase: 35-toolregistry*
*Completed: 2026-04-03*
