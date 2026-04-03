---
phase: 32-kernel-generator-tool-runtime-v2
plan: 02
subsystem: core
tags: [protocol, tool-runner, tool-catalog, facade, pydantic, runtime-spec]

# Dependency graph
requires:
  - phase: 32-01
    provides: Tool Runtime v2 type system (ToolSpec, ToolBinding, ToolInstance, ToolPlane, ToolDecision)
provides:
  - ToolRunner Protocol + InlineToolRunner transition implementation
  - ToolCatalog facade over ToolRegistry with base+overlay versioning
  - AgentRuntimeSpec extended with 5 Tool Runtime v2 optional fields
affects: [32-03 Kernel generator, 33 ToolRunner full impl, 34 Exp/Service, 35 constraint migration]

# Tech tracking
tech-stack:
  added: []
  patterns: [runtime_checkable Protocol for ToolRunner, facade pattern for ToolCatalog over ToolRegistry, TYPE_CHECKING + Any for circular-import-safe Pydantic fields]

key-files:
  created:
    - matmaster/core/tool_runner.py
    - matmaster/tools/tool_catalog.py
    - tests/matmaster/core/test_tool_runner.py
    - tests/matmaster/tools/test_tool_catalog.py
  modified:
    - matmaster/types/runtime.py
    - tests/matmaster/types/test_runtime.py

key-decisions:
  - "ToolRunner/ToolCatalog/RuntimeTopology typed as Any at Pydantic runtime to avoid circular import through types/__init__ -> runtime -> tool_runner -> guard_pipeline -> types"
  - "TYPE_CHECKING block preserves static type info for IDEs/mypy while runtime uses Any"
  - "InlineToolRunner wraps existing 3-phase guard->hook->execute chain as standalone ToolRunner"

patterns-established:
  - "ToolRunner @runtime_checkable Protocol: execute_batch(tool_calls, ctx, on_result) -> list[(ToolCallData, ToolResult)]"
  - "ToolCatalog facade: wraps ToolRegistry with version tracking for lazy MCP injection"
  - "AgentRuntimeSpec v2 field pattern: Any at runtime + TYPE_CHECKING imports for circular-import-safe extension"

requirements-completed: [TCAT-01, TCAT-02, TCAT-03, TRUN-01, TRUN-02, TCON-02, SPEC-01]

# Metrics
duration: 7min
completed: 2026-04-02
---

# Phase 32 Plan 02: ToolRunner Protocol + ToolCatalog + AgentRuntimeSpec Extension Summary

**ToolRunner Protocol with InlineToolRunner 3-phase execution chain, ToolCatalog base+overlay facade, and AgentRuntimeSpec 5-field extension for Tool Runtime v2**

## Performance

- **Duration:** 7 min
- **Started:** 2026-04-02T09:40:56Z
- **Completed:** 2026-04-02T09:48:22Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- Created ToolRunner @runtime_checkable Protocol defining the tool execution strategy interface
- Implemented InlineToolRunner as Phase 1 transition wrapping the existing agent.py guard -> pre_hook -> parallel_execute -> post_hook chain
- Created ToolCatalog facade over ToolRegistry with base+overlay structure and version tracking for MCP lazy injection
- Extended AgentRuntimeSpec with 5 optional fields (tool_runner, tool_catalog, runtime_topology, capability_policy, structural_validation)
- 102 plan-related tests passing, 1346 total matmaster tests passing (5 pre-existing failures unrelated)

## Task Commits

Each task was committed atomically:

1. **Task 1: ToolRunner + ToolCatalog (TDD RED)** - `efa71e10` (test)
2. **Task 1: ToolRunner + ToolCatalog (TDD GREEN)** - `18537764` (feat)
3. **Task 2: AgentRuntimeSpec extension** - `ef1d2c1b` (feat)

_Note: Task 1 followed TDD flow with separate RED and GREEN commits_

## Files Created/Modified

### Created
- `matmaster/core/tool_runner.py` - ToolRunner Protocol, ToolExecutionContext dataclass, InlineToolRunner implementation
- `matmaster/tools/tool_catalog.py` - ToolCatalog facade with version tracking, get_tool() -> ToolInstance, build_definitions()
- `tests/matmaster/core/test_tool_runner.py` - 15 tests: protocol check, guard deny, hook skip, parallel execution, callbacks, order, post-hook
- `tests/matmaster/tools/test_tool_catalog.py` - 10 tests: version tracking, definitions, get_tool, container protocol

### Modified
- `matmaster/types/runtime.py` - Added TYPE_CHECKING imports + 5 optional fields to AgentRuntimeSpec
- `tests/matmaster/types/test_runtime.py` - 6 new tests for v2 fields: defaults, backward compat, type acceptance, model_dump

## Decisions Made
- **Any at Pydantic runtime for v2 fields**: Circular import chain (types/__init__ -> runtime -> tool_runner -> guard_pipeline -> types) prevents direct type usage. TYPE_CHECKING block provides full static type info while runtime uses Any. Same pattern as existing AgentRuntime.kernel: Any.
- **InlineToolRunner wraps existing logic**: Direct extraction of agent.py L217-311 three-phase pattern (serial guard+hook -> parallel execute -> serial post-hook) into a standalone ToolRunner. No behavioral changes.
- **ToolCatalog accesses registry internals**: get_tool() reads _tools and _sources dicts to construct ToolInstance. Acceptable for Phase 1 facade; Phase 35 will clean this up when ToolRegistry degrades to pure storage.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Circular import resolution for AgentRuntimeSpec v2 fields**
- **Found during:** Task 2 (AgentRuntimeSpec extension)
- **Issue:** Plan specified `tool_runner: ToolRunner | None = None` with TYPE_CHECKING import, but Pydantic v2 with `from __future__ import annotations` cannot resolve forward refs at model instantiation time when the TYPE_CHECKING imports create a circular chain through types/__init__.py
- **Fix:** Changed field annotations to `Any | None = None` at runtime, kept TYPE_CHECKING block for static analysis. Same pattern as existing `AgentRuntime.kernel: Any`
- **Files modified:** matmaster/types/runtime.py
- **Verification:** 77 runtime+kernel tests pass, all 5 fields default to None, InlineToolRunner accepted as tool_runner value
- **Committed in:** ef1d2c1b (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 Rule 3 blocking)
**Impact on plan:** Annotation change is cosmetic -- runtime behavior identical. Static type checkers still see full types via TYPE_CHECKING block.

## Issues Encountered
- pytest not installed in worktree .venv -- resolved by `uv add --dev pytest pytest-asyncio`
- 5 pre-existing test failures unchanged (web_search rename, bohrium skill path, import audit, real API tests)

## Known Stubs
None -- all implementations are complete with full test coverage.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- ToolRunner Protocol available for Kernel to consume via `spec.tool_runner`
- ToolCatalog ready for Kernel version-check loop to detect tool set changes
- AgentRuntimeSpec fields ready for Exp.assemble() to populate
- InlineToolRunner ready to serve as default runner when `spec.tool_runner is None`
- All types and implementations ready for Plan 03 (Kernel generator + _resolve_tool_definitions)

---
## Self-Check: PASSED

All 6 key files verified present. All 3 commit hashes verified in git log.

---
*Phase: 32-kernel-generator-tool-runtime-v2*
*Completed: 2026-04-02*
