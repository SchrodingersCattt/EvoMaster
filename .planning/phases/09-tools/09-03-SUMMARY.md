---
phase: 09-tools
plan: 03
subsystem: tools
tags: [builtin-tool, exp-assembly, tool-registry, read-tracker, editor-removal, toml-config]

# Dependency graph
requires:
  - phase: 09-tools-01
    provides: ReadTracker, ReadTool, WriteTool, EditTool implementations
  - phase: 09-tools-02
    provides: GlobTool, GrepTool implementations
provides:
  - Complete 12-tool native builtin registration in Exp._init_builtin_tools
  - ReadTracker lifecycle management (create in build_runtime, cleanup registered)
  - EditorTool fully removed from Exp assembly
  - Explicit tool enumeration in direct.toml (replaces wildcard)
  - Non-wildcard build_runtime condition (any non-empty list triggers init)
affects: [agent-runtime, tool-registry, exp-config]

# Tech tracking
tech-stack:
  added: []
  patterns: [explicit-tool-enumeration in TOML, ReadTracker cleanup via _register_cleanup]

key-files:
  created: []
  modified:
    - matmaster/tools/builtin/__init__.py
    - matmaster/core/exp.py
    - matmaster/config/exp.py
    - matmaster/exps/direct.toml
    - tests/matmaster/core/test_exp.py

key-decisions:
  - "build_runtime condition changed from wildcard-only to any non-empty list (supports explicit enumeration)"
  - "EditorTool (str_replace_editor) fully removed from Exp, replaced by native ReadTool/WriteTool/EditTool"
  - "ReadTracker.clear registered as cleanup callback to reset state between agent runs"

patterns-established:
  - "Explicit tool enumeration: direct.toml lists all 12 tool names instead of wildcard"
  - "ReadTracker lifecycle: created in _init_builtin_tools, injected into Read/Write/Edit, cleanup registered"

requirements-completed: [TOOL-01, TOOL-02, TOOL-03, TOOL-05, TOOL-06, TOOL-08]

# Metrics
duration: 6min
completed: 2026-03-25
---

# Phase 09 Plan 03: Tool Wiring Summary

**Wire 12 native tools into Exp assembly with ReadTracker lifecycle, remove EditorTool adapter, switch direct.toml to explicit enumeration**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-25T04:22:01Z
- **Completed:** 2026-03-25T04:27:38Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Exp._init_builtin_tools now registers 12 native tools (was 7) with ReadTracker shared instance for Read-Before-Modify protocol
- EditorTool (str_replace_editor) completely removed from Exp assembly -- replaced by native ReadTool/WriteTool/EditTool
- MonitorJobTool retained as the sole evo adapter tool (source='builtin_evo')
- direct.toml switched from wildcard to explicit 12-tool enumeration
- build_runtime condition updated to accept any non-empty builtin list (not just wildcard)
- 11 integration tests covering: 12 native count, 1 evo adapter, EditorTool absent, MonitorJobTool retained, ReadTracker cleanup, explicit config, empty config

## Task Commits

Each task was committed atomically:

1. **Task 1: Wire tools into Exp + remove EditorTool + update build_runtime** - `7330676` (feat)
2. **Task 2: Explicit tool enumeration + integration tests** - `04f9093` (feat)

## Files Created/Modified
- `matmaster/tools/builtin/__init__.py` - Added GlobTool/GrepTool exports (ReadTool/WriteTool/EditTool/ReadTracker already present from Plan 01)
- `matmaster/core/exp.py` - Refactored _init_builtin_tools (12 native + 1 evo), removed EditorTool, updated build_runtime condition
- `matmaster/config/exp.py` - Added documentation comment for explicit tool list support
- `matmaster/exps/direct.toml` - Replaced wildcard with explicit 12-tool name list
- `tests/matmaster/core/test_exp.py` - Updated TestExpBuiltinTools: 11 tests for 12-tool registry, EditorTool removal, ReadTracker cleanup

## Decisions Made
- Changed build_runtime condition from `"*" in builtin_cfg` to `builtin_cfg` (truthiness check) -- supports both explicit list and future wildcard usage
- EditorTool import and registration fully removed (not just commented) since native tools provide complete replacement
- ReadTracker.clear registered via _register_cleanup to guarantee state reset between agent runs

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## Known Stubs

None - all wiring is complete with no placeholder data or mock implementations.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All 12 native builtin tools fully registered and tested in Exp assembly
- Phase 09 tools deliverable complete: ReadTracker + 5 file/search tools + integration wiring
- MonitorJobTool retained for science workflows
- 771 matmaster tests pass with 0 failures

## Self-Check: PASSED

All 5 modified files verified present. Both task commits (7330676, 04f9093) confirmed in git log.

---
*Phase: 09-tools*
*Completed: 2026-03-25*
