---
phase: 03-exp-assembly-layer
plan: 01
subsystem: assembly
tags: [protocol, tool-registry, pydantic, runtime-checkable]

requires:
  - phase: 01-foundation-contracts
    provides: Guard Protocol pattern, AgentRuntimeSpec model
  - phase: 02-agent-kernel
    provides: AgentKernel consuming spec.tool_registry.execute() and get_tool_definitions()
provides:
  - Tool @runtime_checkable Protocol (name, description, json_schema, execute)
  - ToolRegistry class with flat-namespace registration, source tracking, execute dispatch
  - AgentRuntimeSpec.tool_registry typed as ToolRegistry | None
affects: [03-exp-assembly-layer, 04-playground-layer, 05-integration-quality]

tech-stack:
  added: []
  patterns: [flat-namespace-registry, source-tagged-tools, direct-import-over-type-checking]

key-files:
  created:
    - matmaster/assembly/tool_registry.py
    - tests/matmaster/assembly/__init__.py
    - tests/matmaster/assembly/conftest.py
    - tests/matmaster/assembly/test_tool_registry.py
  modified:
    - matmaster/types/runtime.py
    - tests/matmaster/engine/test_agent.py

key-decisions:
  - "Direct import of ToolRegistry in runtime.py (not TYPE_CHECKING) -- no circular dependency, Pydantic needs runtime class resolution"
  - "Engine tests migrated from MockToolRegistry to real ToolRegistry + _CatchAllTool -- validates type constraint end-to-end"

patterns-established:
  - "Tool Protocol: @runtime_checkable with name/description/json_schema properties + execute method"
  - "ToolRegistry: dict-based flat namespace with source tags, override warning via logger.warning"
  - "_CatchAllTool pattern: test tool that records calls for assertion in engine tests"

requirements-completed: [ASBL-02]

duration: 5min
completed: 2026-03-22
---

# Phase 3 Plan 01: Tool Protocol + ToolRegistry Summary

**@runtime_checkable Tool Protocol with flat-namespace ToolRegistry providing source-tagged registration, execute dispatch, and OpenAI function calling definitions**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-22T02:52:14Z
- **Completed:** 2026-03-22T02:57:31Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- Tool Protocol defined with @runtime_checkable -- isinstance checks work for builtin/MCP/skill tool implementations
- ToolRegistry with dict-based flat namespace, source tracking (builtin/mcp/skill), same-name override warning
- AgentRuntimeSpec.tool_registry typed as ToolRegistry | None (was Any), no circular import
- Engine tests updated to use real ToolRegistry, validating type constraint end-to-end

## Task Commits

Each task was committed atomically:

1. **Task 1: Tool Protocol + ToolRegistry with tests (TDD RED)** - `c65b0f2` (test)
2. **Task 1: Tool Protocol + ToolRegistry implementation (TDD GREEN)** - `0297f17` (feat, adopted by parallel 03-02 agent)
3. **Task 2: Update AgentRuntimeSpec.tool_registry type** - `92f0600` (feat)

_Note: TDD task had separate RED and GREEN commits._

## Files Created/Modified
- `matmaster/assembly/tool_registry.py` - Tool Protocol + ToolRegistry class with flat namespace, source tracking, execute dispatch
- `matmaster/types/runtime.py` - tool_registry field typed as ToolRegistry | None (was Any)
- `tests/matmaster/assembly/__init__.py` - Test package init
- `tests/matmaster/assembly/conftest.py` - MockTool fixture satisfying Tool Protocol
- `tests/matmaster/assembly/test_tool_registry.py` - 11 tests covering all ToolRegistry behaviors
- `tests/matmaster/engine/test_agent.py` - Migrated from MockToolRegistry to real ToolRegistry + _CatchAllTool

## Decisions Made

1. **Direct import over TYPE_CHECKING** -- The plan specified TYPE_CHECKING guard for ToolRegistry import in runtime.py, but Pydantic needs the class at runtime for validation. Since tool_registry.py does not import from matmaster.types, no circular dependency exists. Direct import is correct.

2. **Engine test migration** -- Changed engine tests from using a duck-typed MockToolRegistry to real ToolRegistry instances with registered _CatchAllTool objects. This validates the type constraint works end-to-end and prevents future regressions.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed Pydantic TYPE_CHECKING incompatibility**
- **Found during:** Task 2 (AgentRuntimeSpec type update)
- **Issue:** Plan specified TYPE_CHECKING guard for ToolRegistry import, but Pydantic cannot resolve string annotations from TYPE_CHECKING blocks at runtime -- raises PydanticUserError
- **Fix:** Used direct import instead (verified no circular dependency exists between assembly and types modules)
- **Files modified:** matmaster/types/runtime.py
- **Verification:** `python -c "from matmaster.types.runtime import AgentRuntimeSpec"` succeeds
- **Committed in:** 92f0600 (Task 2 commit)

**2. [Rule 1 - Bug] Fixed engine test MockToolRegistry type mismatch**
- **Found during:** Task 2 (regression test)
- **Issue:** Engine tests used duck-typed MockToolRegistry, which fails Pydantic's isinstance validation against ToolRegistry class
- **Fix:** Replaced MockToolRegistry with real ToolRegistry + _CatchAllTool pattern, keeping same assertion capability via tool.calls tracking
- **Files modified:** tests/matmaster/engine/test_agent.py
- **Verification:** All 69 engine tests pass
- **Committed in:** 92f0600 (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (2 bugs)
**Impact on plan:** Both fixes necessary for correctness. TYPE_CHECKING approach is fundamentally incompatible with Pydantic's runtime validation. No scope creep.

## Issues Encountered
None beyond the auto-fixed deviations.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- ToolRegistry ready for consumption by Exp.assemble() (Plan 02: ContextBuilder, Plan 03: Exp base class)
- AgentRuntimeSpec.tool_registry properly typed -- kernel validates ToolRegistry at spec construction time
- All tests green: 27 assembly + 69 engine = 96 total

## Self-Check: PASSED

- All 6 created/modified files exist on disk
- All 3 commits (c65b0f2, 0297f17, 92f0600) found in git history
- 96 tests pass (27 assembly + 69 engine)

---
*Phase: 03-exp-assembly-layer*
*Completed: 2026-03-22*
