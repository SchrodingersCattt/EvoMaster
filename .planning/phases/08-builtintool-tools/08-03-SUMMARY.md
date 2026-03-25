---
phase: 08-builtintool-tools
plan: 03
subsystem: tools
tags: [builtin-tools, tool-registry, exp-assembly, dual-source]

requires:
  - phase: 08-builtintool-tools (plan 01)
    provides: BashTool, ListDirTool native implementations
  - phase: 08-builtintool-tools (plan 02)
    provides: TaskCreate/Get/List/Update/Complete tool suite
provides:
  - Exp._init_builtin_tools dual-source registration (native + evo adapter)
  - Integration tests verifying 7 native + 2 evo adapter tools
affects: [phase-09-editor-tools, exp-assembly, builtin-tool-migration]

tech-stack:
  added: []
  patterns: [dual-source tool registration, source tag labeling]

key-files:
  created: []
  modified:
    - matmaster/core/exp.py
    - tests/matmaster/core/test_exp.py

key-decisions:
  - "Native tools use source='builtin', evo adapter tools use source='builtin_evo' for clear provenance tracking"
  - "__init__.py already had correct exports from Plan 01/02 -- no changes needed"

patterns-established:
  - "Dual-source registration: native BuiltinTool (source='builtin') + EvoToolAdapter (source='builtin_evo') in _init_builtin_tools"
  - "Source tag convention: 'builtin' for native, 'builtin_evo' for transitional evo adapter tools"

requirements-completed: [TOOL-04, TOOL-07, TOOL-09]

duration: 3min
completed: 2026-03-25
---

# Phase 08 Plan 03: Exp Integration Summary

**Exp._init_builtin_tools refactored to dual-source registration: 7 native BuiltinTools (source='builtin') + 2 EvoToolAdapter tools (source='builtin_evo'), with 5 integration tests**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-25T02:34:33Z
- **Completed:** 2026-03-25T02:37:20Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Refactored _init_builtin_tools from 3 evo-wrapped tools to 7 native + 2 evo adapter dual-source registration
- Source tag labeling: "builtin" vs "builtin_evo" for clear provenance tracking
- 5 integration tests covering native tools, evo adapter tools, total count, tool names, and session=None guard

## Task Commits

Each task was committed atomically:

1. **Task 1: Update __init__.py + refactor _init_builtin_tools** - `99f7a26` (feat)
2. **Task 2: Integration tests for dual-source registration** - `bdefa67` (test)

## Files Created/Modified
- `matmaster/core/exp.py` - Refactored _init_builtin_tools for dual-source registration
- `tests/matmaster/core/test_exp.py` - Added TestExpBuiltinTools class with 5 tests

## Decisions Made
- Native tools use source="builtin", evo adapter tools use source="builtin_evo" -- enables registry.get_tools_by_source() queries for provenance tracking
- __init__.py already had correct exports from Plan 01/02 waves, no modification needed

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

None.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Phase 08 complete: all 3 plans delivered
- 7 native BuiltinTools + 2 transitional EvoToolAdapter tools registered in Exp assembly
- Phase 09 can replace EditorTool (currently source="builtin_evo") with native Read/Write/Edit tools
- MonitorJobTool retained as evo adapter (science-specific, no native migration planned)

---
*Phase: 08-builtintool-tools*
*Completed: 2026-03-25*
