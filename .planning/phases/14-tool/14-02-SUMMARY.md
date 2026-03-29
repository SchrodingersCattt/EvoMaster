---
phase: 14-tool
plan: 02
subsystem: tools
tags: [asyncio, to_thread, lazy-mcp, skill-tool, evo-adapter, kernel-bridge, async-protocol]

# Dependency graph
requires:
  - phase: 14-tool-01
    provides: "BuiltinTool.execute() async pattern (applied inline as prereq)"
  - phase: 13-llm-provider
    provides: "_sync_call_async bridge pattern concept"
provides:
  - "LazyMCPTool.execute() async def with asyncio.to_thread wrapping sync connector/tool calls"
  - "SkillTool.execute() async def with to_thread wrapping _execute_sync() helper"
  - "EvoToolAdapter.execute() async def with to_thread wrapping _execute_sync() helper"
  - "Kernel tool dispatch via _sync_call_async bridge for async ToolRegistry.execute()"
  - "All tool test files async-migrated (235 tests passing)"
affects: [15-builtin-tools, 17-kernel-async]

# Tech tracking
tech-stack:
  added:
    - "pytest-asyncio>=0.25.0 (dev dependency)"
  patterns:
    - "async execute() + sync _execute_sync() helper pattern for non-BuiltinTool classes"
    - "_sync_call_async bridge: dedicated daemon event loop + run_coroutine_threadsafe"
    - "Mechanical async test migration: def test -> async def test, execute() -> await execute()"

key-files:
  created: []
  modified:
    - matmaster/tools/lazy_mcp.py
    - matmaster/tools/skill_tool.py
    - matmaster/tools/evomaster_tool_adapter.py
    - matmaster/core/agent.py
    - matmaster/tools/builtin/base.py
    - matmaster/tools/tool_registry.py
    - pyproject.toml
    - tests/matmaster/tools/conftest.py
    - tests/matmaster/tools/test_builtin_base.py
    - tests/matmaster/tools/test_tool_registry.py
    - tests/matmaster/tools/test_lazy_mcp.py
    - tests/matmaster/tools/test_evomaster_tool_adapter.py
    - tests/test_skill_tool.py
    - tests/matmaster/tools/test_bash_tool.py
    - tests/matmaster/tools/test_read_tool.py
    - tests/matmaster/tools/test_write_tool.py
    - tests/matmaster/tools/test_edit_tool.py
    - tests/matmaster/tools/test_glob_tool.py
    - tests/matmaster/tools/test_grep_tool.py
    - tests/matmaster/tools/test_listdir_tool.py
    - tests/matmaster/tools/test_spawn_tool.py
    - tests/matmaster/tools/test_task_tools.py

key-decisions:
  - "LazyMCPTool: two separate asyncio.to_thread calls for connect_and_get_tool and real_tool.execute (json.dumps is pure CPU, no wrapping needed)"
  - "SkillTool/EvoToolAdapter: single asyncio.to_thread wrapping entire _execute_sync() helper method"
  - "Kernel bridge: dedicated daemon thread event loop (_bridge_loop) with _sync_call_async using run_coroutine_threadsafe"
  - "test_skill_tool_callback.py NOT migrated (tests evomaster's SkillTool, not matmaster's)"
  - "Web tool tests (test_web_search_tool.py, test_web_fetch_tool.py) not in worktree -- skipped"

patterns-established:
  - "_execute_sync() helper pattern: extract sync method body into helper, async execute() wraps via to_thread"
  - "_sync_call_async kernel bridge: module-level daemon loop + helper function, to be removed in Phase 17"
  - "Mechanical async test migration: only methods calling execute() become async def + await"

requirements-completed: [TOOL-01]

# Metrics
duration: 14min
completed: 2026-03-27
---

# Phase 14 Plan 02: Non-BuiltinTool Async + Kernel Bridge + Full Test Migration Summary

**LazyMCPTool/SkillTool/EvoToolAdapter async execute() via to_thread, Kernel _sync_call_async bridge, 235 tool tests async-migrated**

## Performance

- **Duration:** 14 min
- **Started:** 2026-03-27T06:35:29Z
- **Completed:** 2026-03-27T06:50:16Z
- **Tasks:** 2
- **Files modified:** 23

## Accomplishments
- LazyMCPTool.execute() converted to async def with granular asyncio.to_thread wrapping for connector and tool calls
- SkillTool.execute() and EvoToolAdapter.execute() converted to async def with _execute_sync() helper pattern
- AgentKernel tool dispatch now uses _sync_call_async bridge (dedicated daemon event loop) to call async ToolRegistry.execute()
- All 235 tool tests passing with async execute() -- full mechanical migration across 15+ test files

## Task Commits

Each task was committed atomically:

1. **Task 1: LazyMCPTool + SkillTool + EvoToolAdapter async execute + Kernel bridge** - `4535945` (feat)
2. **Task 2: Full tool test async migration + Plan 01 prereqs** - `482ac62` (test)

## Files Created/Modified
- `matmaster/tools/lazy_mcp.py` - LazyMCPTool.execute() async def, two to_thread calls for connector and real_tool
- `matmaster/tools/skill_tool.py` - SkillTool.execute() async def + _execute_sync() helper, added import asyncio
- `matmaster/tools/evomaster_tool_adapter.py` - EvoToolAdapter.execute() async def + _execute_sync() helper, added import asyncio
- `matmaster/core/agent.py` - _bridge_loop + _bridge_thread + _sync_call_async() function, tool dispatch wrapped
- `matmaster/tools/builtin/base.py` - BuiltinTool.execute() async def with asyncio.to_thread (Plan 01 prereq)
- `matmaster/tools/tool_registry.py` - ToolRegistry.execute() async def, Tool Protocol execute() async (Plan 01 prereq)
- `pyproject.toml` - Added pytest-asyncio>=0.25.0 to dev dependencies
- `tests/matmaster/tools/conftest.py` - MockTool.execute() async def
- `tests/matmaster/tools/test_builtin_base.py` - execute() tests async
- `tests/matmaster/tools/test_tool_registry.py` - execute() tests async, helper tools async
- `tests/matmaster/tools/test_lazy_mcp.py` - 6 execution tests async
- `tests/matmaster/tools/test_evomaster_tool_adapter.py` - 7 execution tests async
- `tests/test_skill_tool.py` - 10 execution tests async
- `tests/matmaster/tools/test_bash_tool.py` - 5 execution tests async
- `tests/matmaster/tools/test_read_tool.py` - 17 execution tests async
- `tests/matmaster/tools/test_write_tool.py` - 7 execution tests async
- `tests/matmaster/tools/test_edit_tool.py` - 10 execution tests async
- `tests/matmaster/tools/test_glob_tool.py` - 8 execution tests async
- `tests/matmaster/tools/test_grep_tool.py` - 9 execution tests async
- `tests/matmaster/tools/test_listdir_tool.py` - 4 execution tests async
- `tests/matmaster/tools/test_spawn_tool.py` - 7 execution tests async
- `tests/matmaster/tools/test_task_tools.py` - 15 execution tests async

## Decisions Made
- LazyMCPTool uses two separate to_thread calls (one for connect, one for execute) rather than wrapping entire method, since json.dumps is pure CPU and doesn't need offloading
- SkillTool and EvoToolAdapter use _execute_sync() helper pattern -- entire sync body extracted to helper, execute() is thin async wrapper
- Kernel bridge uses a module-level dedicated daemon thread event loop with asyncio.run_coroutine_threadsafe, matching the pattern described in RESEARCH.md
- test_skill_tool_callback.py left unchanged -- it tests evomaster.agent.tools.skill.SkillTool, not matmaster's SkillTool

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Applied Plan 01 prerequisite changes to worktree**
- **Found during:** Task 2 (test execution)
- **Issue:** BuiltinTool.execute() and ToolRegistry.execute() were still sync def in this worktree (Plan 01 changes not present). await tool.execute() in tests raised TypeError: object str can't be used in await expression
- **Fix:** Applied Plan 01 changes inline: BuiltinTool.execute() async with to_thread, ToolRegistry.execute() async with await-then-normalize, Tool Protocol async, MockTool async
- **Files modified:** matmaster/tools/builtin/base.py, matmaster/tools/tool_registry.py, tests/matmaster/tools/conftest.py, tests/matmaster/tools/test_builtin_base.py, tests/matmaster/tools/test_tool_registry.py
- **Verification:** 235 tests pass
- **Committed in:** 482ac62 (Task 2 commit)

**2. [Rule 3 - Blocking] Added pytest-asyncio to dev dependencies**
- **Found during:** Task 2 (test execution)
- **Issue:** pytest-asyncio not in pyproject.toml dev extras. async def test methods raised "async functions are not natively supported" despite asyncio_mode=auto in pytest.ini
- **Fix:** Added pytest-asyncio>=0.25.0 to [project.optional-dependencies] dev section, ran uv sync
- **Files modified:** pyproject.toml, uv.lock
- **Verification:** All async tests recognized and pass
- **Committed in:** 482ac62 (Task 2 commit)

**3. [Rule 3 - Blocking] Skipped non-existent test files**
- **Found during:** Task 2 (file reading)
- **Issue:** test_web_search_tool.py and test_web_fetch_tool.py listed in plan do not exist in this worktree (WebSearchTool/WebFetchTool not present)
- **Fix:** Skipped these files -- they will be created when the web tools are added
- **Files modified:** none
- **Verification:** N/A

---

**Total deviations:** 3 auto-fixed (3 blocking)
**Impact on plan:** All auto-fixes necessary for correct async execution. Plan 01 prereqs were required but not present in parallel worktree. No scope creep.

## Issues Encountered
- Parallel worktree did not contain Plan 01 changes (separate agent execution). Applied the same changes inline to unblock.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All Tool implementations satisfy async Tool Protocol (async def execute())
- Kernel bridge (_sync_call_async) in place for Phase 17 async Kernel migration (will be removed when Kernel becomes async)
- 14 BuiltinTool subclasses have sync _execute() preserved -- ready for individual async optimization in Phase 15
- spawn_fn remains sync Callable per D-07/D-08 (TOOL-05 deferred to Phase 18)

## Self-Check: PASSED

All 6 key source files verified present. Both task commits (4535945, 482ac62) verified in git log. 235 tests collected and passing. Zero async _execute in builtin/ directory.

---
*Phase: 14-tool*
*Completed: 2026-03-27*
