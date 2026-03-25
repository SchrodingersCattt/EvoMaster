---
phase: 08-builtintool-tools
plan: 01
subsystem: tools
tags: [builtin-tool, abc, bash, session, tool-protocol]

requires:
  - phase: 07
    provides: ToolRegistry + Tool Protocol + EvoToolAdapter pattern
provides:
  - BuiltinTool ABC base class with template-method execute and _require_session guard
  - BashTool (session-dependent, bash_safety reuse, proxy clear)
  - ListDirTool (session-dependent, ls -la with error handling)
affects: [08-02, 08-03, 09-subagent, 10-prompt]

tech-stack:
  added: []
  patterns: [BuiltinTool ABC template-method, ClassVar for Protocol satisfaction, session constructor injection]

key-files:
  created:
    - matmaster/tools/builtin/__init__.py
    - matmaster/tools/builtin/base.py
    - matmaster/tools/builtin/bash_tool.py
    - matmaster/tools/builtin/listdir_tool.py
    - tests/matmaster/tools/test_builtin_base.py
    - tests/matmaster/tools/test_bash_tool.py
    - tests/matmaster/tools/test_listdir_tool.py
  modified: []

key-decisions:
  - "BuiltinTool uses ClassVar for name/description/json_schema to satisfy Tool Protocol via structural subtyping"
  - "Session injection via constructor keyword arg (session=None default), consistent with EvoToolAdapter pattern"
  - "Reused evomaster bash_safety.is_dangerous_bash_command directly, no duplication"

patterns-established:
  - "BuiltinTool ABC: subclass declares ClassVar[str] name/description, ClassVar[dict] json_schema, implements _execute()"
  - "Template method: execute() wraps _execute() with try/except, returns 'Error: ...' on failure"
  - "_require_session() guard: call at _execute() start, raises RuntimeError with tool name if session missing"

requirements-completed: [TOOL-04, TOOL-07]

duration: 3min
completed: 2026-03-25
---

# Phase 08 Plan 01: BuiltinTool Base + BashTool + ListDirTool Summary

**BuiltinTool ABC with template-method execute pattern, BashTool with bash_safety check and proxy clear, ListDirTool with ls -la error handling**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-25T02:26:51Z
- **Completed:** 2026-03-25T02:30:00Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 7

## Accomplishments
- BuiltinTool ABC base class satisfying Tool Protocol with template-method execute() and _require_session() guard
- BashTool executing commands via session.exec_bash with dangerous command blocking (reusing evomaster bash_safety), proxy clear prefix, and is_input mode support
- ListDirTool executing ls -la via session with error handling and default path fallback
- 20 unit tests covering Protocol compliance, error handling, session injection, and all execution paths

## Task Commits

Each task was committed atomically:

1. **Task 1 (RED): Failing tests** - `c450424` (test)
2. **Task 1 (GREEN): Implementation** - `b95afbb` (feat)

**Plan metadata:** TBD (docs: complete plan)

_Note: TDD task with RED + GREEN commits._

## Files Created/Modified
- `matmaster/tools/builtin/__init__.py` - Package exports: BuiltinTool, BashTool, ListDirTool
- `matmaster/tools/builtin/base.py` - BuiltinTool ABC with execute template method and _require_session guard
- `matmaster/tools/builtin/bash_tool.py` - BashTool: session-based bash execution with safety checks
- `matmaster/tools/builtin/listdir_tool.py` - ListDirTool: session-based directory listing
- `tests/matmaster/tools/test_builtin_base.py` - 7 tests: Protocol, _require_session, execute error handling
- `tests/matmaster/tools/test_bash_tool.py` - 7 tests: execution, safety, is_input, no-session
- `tests/matmaster/tools/test_listdir_tool.py` - 6 tests: listing, error, default path, no-session

## Decisions Made
- Used ClassVar for name/description/json_schema to satisfy Tool Protocol via structural subtyping (properties not needed since ClassVar access works as attributes)
- Session injection via constructor keyword arg with None default, matching EvoToolAdapter pattern
- Reused evomaster bash_safety directly via import rather than duplicating logic

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Known Stubs
None - all functionality is fully wired.

## Next Phase Readiness
- BuiltinTool base class pattern established for Plan 02 (FileReadTool, FileEditTool, FileWriteTool)
- Session injection pattern and _require_session guard ready for reuse
- 08-02 and 08-03 can proceed with the same BuiltinTool ABC

---
*Phase: 08-builtintool-tools*
*Completed: 2026-03-25*
