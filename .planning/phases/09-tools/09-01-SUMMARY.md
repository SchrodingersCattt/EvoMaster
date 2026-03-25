---
phase: 09-tools
plan: 01
subsystem: tools
tags: [builtin-tool, read-tracker, read-before-modify, str-replace, file-operations]

requires:
  - phase: 08-builtin-tool-infra
    provides: BuiltinTool ABC base class, Tool Protocol, ToolRegistry, BashTool/ListDirTool patterns
provides:
  - ReadTracker shared state for Read-Before-Modify protocol
  - ReadTool (read_file) with line-numbered output and line_range support
  - WriteTool (write_file) with RBM enforcement for existing files
  - EditTool (edit_file) with str_replace unique-match and strip fallback
affects: [09-02, 09-03, exp-assemble, agent-runtime]

tech-stack:
  added: []
  patterns: [tracker-injection via constructor kwarg, posixpath.normpath for remote paths, strip-retry fallback]

key-files:
  created:
    - matmaster/tools/builtin/read_tracker.py
    - matmaster/tools/builtin/read_tool.py
    - matmaster/tools/builtin/write_tool.py
    - matmaster/tools/builtin/edit_tool.py
    - tests/matmaster/tools/test_read_tracker.py
    - tests/matmaster/tools/test_read_tool.py
    - tests/matmaster/tools/test_write_tool.py
    - tests/matmaster/tools/test_edit_tool.py
  modified:
    - matmaster/tools/builtin/__init__.py

key-decisions:
  - "Import SNIPPET_LINES/maybe_truncate/MAX_OUTPUT_SIZE from evomaster editor module (internal dependency, avoids duplication)"
  - "ReadTracker uses posixpath.normpath for all path normalization (remote env is always Linux)"
  - "tracker=None disables Read-Before-Modify enforcement (backward compat, non-protocol tools)"

patterns-established:
  - "Tracker injection pattern: tools accept optional tracker kwarg in constructor, pass through at Exp assemble time"
  - "Error string D-03 format: \"Error: file '{path}' must be read before modify\" (exact match for tests)"
  - "Strip retry: if old_str not found but old_str.strip() matches uniquely, use stripped version automatically"

requirements-completed: [TOOL-01, TOOL-02, TOOL-03, TOOL-08]

duration: 5min
completed: 2026-03-25
---

# Phase 09 Plan 01: ReadTracker + File Operation Tools Summary

**ReadTracker + ReadTool + WriteTool + EditTool with Read-Before-Modify protocol enforcement, posixpath normalization, and str_replace strip fallback**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-25T04:08:31Z
- **Completed:** 2026-03-25T04:13:22Z
- **Tasks:** 2
- **Files modified:** 9

## Accomplishments
- ReadTracker class with posixpath.normpath normalization for consistent path matching across dot/dotdot variants
- ReadTool reads remote files via session.read_file with cat -n line-numbered output and 1-indexed line_range partial reads
- WriteTool enforces Read-Before-Modify for existing files while allowing new file creation without restriction
- EditTool performs str_replace with unique-match enforcement, multi-match line number reporting, and strip fallback
- 36 unit tests covering all behavior specifications including exact error string format validation

## Task Commits

Each task was committed atomically:

1. **Task 1: ReadTracker + ReadTool + WriteTool + EditTool implementations** - `603f236` (feat)
2. **Task 2: Test suite for ReadTracker + ReadTool + WriteTool + EditTool** - `c6a6349` (test)

## Files Created/Modified
- `matmaster/tools/builtin/read_tracker.py` - ReadTracker shared state (mark_read/has_been_read/clear with posixpath normalization)
- `matmaster/tools/builtin/read_tool.py` - ReadTool with cat -n format, line_range, tracker integration
- `matmaster/tools/builtin/write_tool.py` - WriteTool with Read-Before-Modify enforcement for existing files
- `matmaster/tools/builtin/edit_tool.py` - EditTool with str_replace unique-match, strip fallback, context snippet
- `matmaster/tools/builtin/__init__.py` - Updated exports to include ReadTracker, ReadTool, WriteTool, EditTool
- `tests/matmaster/tools/test_read_tracker.py` - 6 tests: mark/check, clear, normpath dot/dotdot, multi-file
- `tests/matmaster/tools/test_read_tool.py` - 9 tests: full read, line_range, open_end, not_found, tracker, no_session
- `tests/matmaster/tools/test_write_tool.py` - 7 tests: new file, existing w/read, existing w/o read, no_tracker, exact format
- `tests/matmaster/tools/test_edit_tool.py` - 10 tests: unique replace, no match, multi match, same strings, strip fallback, RBM, snippet

## Decisions Made
- Imported SNIPPET_LINES/maybe_truncate/MAX_OUTPUT_SIZE from evomaster.agent.tools.builtin.editor rather than duplicating (evomaster is an internal dependency already on the import path)
- Used posixpath.normpath consistently for all path operations since remote session environment is always Linux
- tracker=None disables Read-Before-Modify enforcement entirely, allowing backward compat for non-protocol usage

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## Known Stubs

None - all tools are fully functional with no placeholder data or mock implementations.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- File operation tools (ReadTool/WriteTool/EditTool) are ready for Exp assemble-time injection
- ReadTracker ready to be shared across Read/Write/Edit tools as a single instance per agent run
- Plan 02 (SubAgent) and Plan 03 (prompt/description) can proceed independently

## Self-Check: PASSED

All 9 files verified present. Both task commits (603f236, c6a6349) confirmed in git log.

---
*Phase: 09-tools*
*Completed: 2026-03-25*
