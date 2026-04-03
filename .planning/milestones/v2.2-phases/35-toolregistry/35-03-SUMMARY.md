---
phase: 35-toolregistry
plan: 03
subsystem: tools
tags: [tool-registry, tool-catalog, context-builder, agent-runtime-spec]

# Dependency graph
requires:
  - phase: 35-01
    provides: ReadBeforeModifyGuard + RunStateGuard in GuardPipeline
  - phase: 35-02
    provides: ToolSpec input_validator + ToolCompiler topology-dependent binding
provides:
  - ToolRegistry demoted to pure storage (register + all_tools + __contains__ + __len__ only)
  - AgentRuntimeSpec without tool_registry field
  - ToolCatalog.inject_stop_event() for cancel propagation
  - ToolCatalog.build_definitions() self-contained OpenAI format
  - ContextBuilder generic tools section (function calling guidance, no per-tool enumeration)
  - agent.py zero spec.tool_registry references (sole path via tool_runner/tool_catalog)
affects: [kernel, exp, context-builder, tool-runner, service-layer]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "ToolCatalog as sole upper-layer facade over ToolRegistry storage"
    - "Generic function calling guidance in system prompt instead of per-tool enumeration"
    - "FullToolRunner as sole tool execution path (no legacy fallback)"

key-files:
  created: []
  modified:
    - matmaster/tools/tool_registry.py
    - matmaster/tools/tool_catalog.py
    - matmaster/types/runtime.py
    - matmaster/core/agent.py
    - matmaster/core/exp.py
    - matmaster/core/context_builder.py
    - matmaster/core/tool_runner.py
    - matmaster/devshell/repl.py
    - src/services/agent_run_service.py

key-decisions:
  - "InlineToolRunner deprecated but retained for test compatibility, updated to use tool_catalog.registry"
  - "ContextBuilder tools section changed from per-tool enumeration to generic function calling guidance"
  - "_SimpleTestToolRunner introduced in test helpers for kernel tests that need guard/hook-aware execution"

patterns-established:
  - "All tool execution goes through ToolRunner -- no direct registry.execute() calls"
  - "Stop event injection uses catalog.inject_stop_event() across all layers (Exp, Service)"
  - "ContextBuilder._build_tools() returns static guidance, tool details in API function definitions"

requirements-completed: [CMIG-04, CMIG-05]

# Metrics
duration: 23min
completed: 2026-04-03
---

# Phase 35 Plan 03: ToolRegistry Demotion Summary

**ToolRegistry demoted to pure storage, ToolCatalog becomes sole upper-layer facade with self-contained build_definitions and inject_stop_event**

## Performance

- **Duration:** 23 min
- **Started:** 2026-04-03T04:42:37Z
- **Completed:** 2026-04-03T05:05:49Z
- **Tasks:** 2
- **Files modified:** 27

## Accomplishments
- ToolRegistry stripped to 4 methods: register, all_tools, __contains__, __len__ (pure storage)
- AgentRuntimeSpec.tool_registry field deleted; all consumers migrated to tool_catalog/tool_runner
- ContextBuilder outputs generic function calling guidance instead of per-tool enumeration
- Exp.run()/run_stream() stop_event injection uses catalog.inject_stop_event()
- 921 core/tools/types/services tests pass (5 pre-existing write_tool failures excluded)

## Task Commits

Each task was committed atomically:

1. **Task 1: ToolRegistry demotion + AgentRuntimeSpec cleanup + Kernel legacy delete** - `9a78a808` (feat)
2. **Task 2: Exp stop_event path switch + ContextBuilder reform + regression** - `4d64ac47` (feat)

## Files Created/Modified
- `matmaster/tools/tool_registry.py` - Pure storage: deleted execute/get_tool_definitions/get_tools_by_source
- `matmaster/tools/tool_catalog.py` - Added inject_stop_event() and self-contained build_definitions()
- `matmaster/types/runtime.py` - Removed tool_registry field from AgentRuntimeSpec
- `matmaster/core/agent.py` - Removed all spec.tool_registry references, legacy path deleted
- `matmaster/core/exp.py` - stop_event via catalog.inject_stop_event(), no tool_registry in spec
- `matmaster/core/context_builder.py` - Generic tools section, no ToolRegistry dependency
- `matmaster/core/tool_runner.py` - InlineToolRunner marked DEPRECATED, updated to use catalog
- `matmaster/devshell/repl.py` - _show_tools uses spec.tool_catalog.registry
- `src/services/agent_run_service.py` - stop_event injection via catalog.inject_stop_event()
- 18 test files updated to remove tool_registry references

## Decisions Made
- InlineToolRunner deprecated but retained for test compatibility -- updated internal execution to use tool_catalog.registry instead of spec.tool_registry
- Created _SimpleTestToolRunner in test helpers as a lightweight ToolRunner for kernel tests that need guard/hook-aware execution without FullToolRunner overhead
- ContextBuilder._build_tools() now always returns a tools section (static guidance) even with no tools -- the function definitions in the API call contain the actual tool details

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] ToolCatalog.build_definitions() delegation to deleted method**
- **Found during:** Task 1
- **Issue:** ToolCatalog.build_definitions() delegated to registry.get_tool_definitions() which was just deleted
- **Fix:** Inlined the OpenAI function calling format generation directly in ToolCatalog
- **Files modified:** matmaster/tools/tool_catalog.py
- **Committed in:** 9a78a808

**2. [Rule 3 - Blocking] DevShell repl.py and agent_run_service.py still referenced spec.tool_registry**
- **Found during:** Task 2
- **Issue:** Production code outside matmaster/core/ still accessed spec.tool_registry for stop_event injection and tool display
- **Fix:** Updated to use spec.tool_catalog.inject_stop_event() and spec.tool_catalog.registry
- **Files modified:** matmaster/devshell/repl.py, src/services/agent_run_service.py
- **Committed in:** 4d64ac47

**3. [Rule 3 - Blocking] 21 test files used spec.tool_registry or AgentRuntimeSpec(tool_registry=...)**
- **Found during:** Task 2
- **Issue:** Extensive test migration needed -- AgentRuntimeSpec field deleted
- **Fix:** Migrated all tests to use tool_catalog/tool_runner; created _SimpleTestToolRunner helper
- **Files modified:** 18 test files
- **Committed in:** 4d64ac47

---

**Total deviations:** 3 auto-fixed (3 blocking)
**Impact on plan:** All fixes necessary for the plan to complete. Scope remained within plan boundaries.

## Issues Encountered
None beyond the deviations documented above.

## User Setup Required
None - no external service configuration required.

## Known Stubs
None - all functionality is wired and operational.

## Next Phase Readiness
- ToolRegistry is now pure storage, ToolCatalog is the sole facade
- Ready for further constraint migration or de-busing work
- Pre-existing test failures in test_full_tool_runner (CapabilityPolicy.evaluate) and integration tests (bus event routing) are independent of this plan

---
## Self-Check: PASSED

All key files exist. Both commit hashes verified in git log.

---
*Phase: 35-toolregistry*
*Completed: 2026-04-03*
