---
phase: 26-tool
plan: 01
subsystem: tools
tags: [bash-safety, editor-helper, evomaster-decoupling, inline]

# Dependency graph
requires: []
provides:
  - matmaster-native bash safety detection (is_dangerous_bash_command, is_dangerous_python_content)
  - matmaster-native editor helpers (SNIPPET_LINES, MAX_OUTPUT_SIZE, maybe_truncate)
  - corrected web_search tool name in eval_tooling_snapshot
affects: [26-tool, 25-session-playground]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Inline evomaster helpers with origin comment markers (---- inlined from ... ----)"

key-files:
  created: []
  modified:
    - matmaster/tools/builtin/bash_tool.py
    - matmaster/tools/builtin/edit_tool.py
    - matmaster/eval_tooling_snapshot.py

key-decisions:
  - "Inlined full bash_safety module including is_dangerous_python_content (not just is_dangerous_bash_command) for completeness"
  - "Used underscore-prefixed module constants (_BLOCKED_FIRST_TOKENS etc.) to avoid namespace pollution"
  - "Preserved evomaster.agent.session.local import in bash_tool.py (Phase 25 scope, not Phase 26)"

patterns-established:
  - "Inline with origin markers: comment blocks marking inlined code source for traceability"

requirements_completed: [TOOL-07, TOOL-08, TOOL-10]

# Metrics
duration: 2min
completed: 2026-04-01
---

# Phase 26 Plan 01: Builtin Tool Helper Internalization Summary

**Inlined bash_safety and editor helpers from evomaster into matmaster native code, eliminating 2 evomaster.agent.tools.builtin imports; fixed web_search naming in eval_tooling_snapshot**

## Performance

- **Duration:** 2 min
- **Started:** 2026-04-01T08:42:39Z
- **Completed:** 2026-04-01T08:44:57Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- bash_tool.py now provides its own is_dangerous_bash_command and is_dangerous_python_content functions with all safety patterns, no longer importing from evomaster.agent.tools.builtin.bash_safety
- edit_tool.py now provides its own SNIPPET_LINES, MAX_OUTPUT_SIZE, and maybe_truncate, no longer importing from evomaster.agent.tools.builtin.editor
- eval_tooling_snapshot.py _BUILTIN_WHEN_STAR list corrected from "web-search" to "web_search" to match matmaster native WebSearchTool.name

## Task Commits

Each task was committed atomically:

1. **Task 1: Inline bash_safety and editor helpers** - `27f3a2fb` (feat)
2. **Task 2: Fix web_search name in eval_tooling_snapshot** - `03c26705` (fix)

## Files Created/Modified
- `matmaster/tools/builtin/bash_tool.py` - Removed evomaster bash_safety import, inlined full safety detection module (constants, patterns, 2 functions)
- `matmaster/tools/builtin/edit_tool.py` - Removed evomaster editor import, inlined SNIPPET_LINES, MAX_OUTPUT_SIZE, maybe_truncate
- `matmaster/eval_tooling_snapshot.py` - Changed "web-search" to "web_search" in _BUILTIN_WHEN_STAR

## Decisions Made
- Inlined the full bash_safety module (including is_dangerous_python_content) rather than only is_dangerous_bash_command, since both functions share the same compiled pattern infrastructure and may be needed by matmaster code
- Used underscore-prefixed names for module-level constants (_BLOCKED_FIRST_TOKENS, _DANGEROUS_COMMAND_PATTERNS, etc.) to signal they are internal implementation details
- Left the evomaster.agent.session.local import on line 76 of bash_tool.py untouched as it belongs to Phase 25 (PLAY-02) scope

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- matmaster/tools/builtin/ no longer has any runtime imports from evomaster.agent.tools.builtin
- Ready for Phase 26 Plan 02 (MCP/calculation path internalization) and Plan 03 (EvoToolAdapter elimination)
- The remaining evomaster import in bash_tool.py (session.local) is tracked for Phase 25

## Self-Check: PASSED

- All 3 modified files exist
- Commit 27f3a2fb (Task 1) exists
- Commit 03c26705 (Task 2) exists
- No evomaster.agent.tools.builtin imports remain in modified files
- All 4 verification commands pass

---
*Phase: 26-tool*
*Completed: 2026-04-01*
