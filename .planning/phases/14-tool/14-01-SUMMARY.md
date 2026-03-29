---
phase: 14-tool
plan: 01
subsystem: tools
tags: [asyncio, to_thread, builtin-tool, tool-registry, async-protocol]

# Dependency graph
requires:
  - phase: 12-protocol
    provides: "async Tool Protocol definition (async def execute in Protocol)"
  - phase: 13-llm-provider
    provides: "async LLMProvider + _sync_call_async bridge pattern"
provides:
  - "BuiltinTool.execute() async def with asyncio.to_thread wrapping sync _execute()"
  - "ToolRegistry.execute() async def awaiting tool.execute()"
  - "MockTool async execute() test fixture"
affects: [14-02, 15-builtin-tools, 17-kernel-async]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "asyncio.to_thread bridge: async execute() delegates to sync _execute()"
    - "await-then-normalize: result = await tool.execute(); return normalize_tool_result(result)"

key-files:
  created: []
  modified:
    - matmaster/tools/builtin/base.py
    - matmaster/tools/tool_registry.py
    - tests/matmaster/tools/conftest.py
    - tests/matmaster/tools/test_builtin_base.py
    - tests/matmaster/tools/test_tool_registry.py

key-decisions:
  - "asyncio.to_thread wraps sync _execute() -- subclasses need zero changes"
  - "await-then-normalize pattern avoids passing coroutine to normalize_tool_result"

patterns-established:
  - "BuiltinTool async bridge: async execute() -> await asyncio.to_thread(self._execute, arguments)"
  - "ToolRegistry await-then-normalize: result = await tool.execute(args); return normalize_tool_result(result)"

requirements-completed: [TOOL-01, TOOL-03, TOOL-04]

# Metrics
duration: 4min
completed: 2026-03-27
---

# Phase 14 Plan 01: BuiltinTool + ToolRegistry Async Summary

**BuiltinTool.execute() async via asyncio.to_thread wrapping sync _execute(), ToolRegistry.execute() async with await-then-normalize pattern**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-27T06:25:37Z
- **Completed:** 2026-03-27T06:30:17Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- BuiltinTool.execute() converted to async def with asyncio.to_thread wrapping sync _execute()
- ToolRegistry.execute() converted to async def, properly awaits tool.execute() before normalizing
- All 14 BuiltinTool subclasses remain untouched (sync _execute() preserved)
- Test fixtures (MockTool) and test suites (21 tests) fully migrated to async, all passing

## Task Commits

Each task was committed atomically:

1. **Task 1: BuiltinTool ABC async execute + ToolRegistry async execute** - `0e38a68` (feat)
2. **Task 2: Test fixtures + test_builtin_base + test_tool_registry async migration** - `bcfc6a4` (test)

## Files Created/Modified
- `matmaster/tools/builtin/base.py` - BuiltinTool.execute() async def + asyncio.to_thread, _execute() stays sync
- `matmaster/tools/tool_registry.py` - ToolRegistry.execute() async def, await tool.execute() then normalize
- `tests/matmaster/tools/conftest.py` - MockTool.execute() now async def
- `tests/matmaster/tools/test_builtin_base.py` - _execute() rolled back to sync, new async execute() tests
- `tests/matmaster/tools/test_tool_registry.py` - All execute() calls now awaited, helper tools async

## Decisions Made
- Used asyncio.to_thread to bridge sync _execute() into async execute() -- keeps all 14 subclasses unchanged
- Applied await-then-normalize pattern (result = await tool.execute(); return normalize_tool_result(result)) to avoid Pitfall 2 (passing coroutine to normalize)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Plan 14-02 can proceed: AgentKernel tool dispatch needs _sync_call_async bridge for async registry.execute()
- All 14 BuiltinTool subclasses ready for Phase 15 individual async migration
- Phase 17 Kernel async will remove the bridge and directly await

## Self-Check: PASSED

All 5 modified files verified present. Both task commits (0e38a68, bcfc6a4) verified in git log. 21 tests pass. Zero async _execute in builtin/ directory.

---
*Phase: 14-tool*
*Completed: 2026-03-27*
