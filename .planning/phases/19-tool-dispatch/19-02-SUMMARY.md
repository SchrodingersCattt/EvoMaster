---
phase: 19-tool-dispatch
plan: 02
subsystem: core
tags: [asyncio, gather, parallel, tool-dispatch, agent-kernel]

# Dependency graph
requires:
  - phase: 17-agentkernel
    provides: "AgentKernel async化 (async run / _run_loop / _call_llm)"
  - phase: 14-tool
    provides: "async ToolRegistry.execute() and async Tool.execute() Protocol"
provides:
  - "Parallel tool dispatch via asyncio.gather in AgentKernel._run_loop()"
  - "Outcome-list pattern for preserving tool_call order across blocked/skipped/executed"
  - "MultiToolProvider test helper for multi-tool_call testing"
  - "6 comprehensive parallel dispatch tests"
affects: [19-tool-dispatch, agent-kernel, tool-dispatch]

# Tech tracking
tech-stack:
  added: []
  patterns: ["outcome-list for ordered parallel dispatch", "closure-based exception boundary with gather defense-in-depth"]

key-files:
  created: []
  modified:
    - "matmaster/core/agent.py"
    - "tests/matmaster/core/test_agent.py"

key-decisions:
  - "Outcome-list 4-tuple pattern for preserving original tool_call order across mixed blocked/skipped/executed states"
  - "Closure catches exceptions as primary strategy; gather return_exceptions=True as defense-in-depth"
  - "post_tool_call hooks fire in batch after gather completes, in original order (accepted behavior change)"

patterns-established:
  - "Outcome-list pattern: Phase 1 serial gate -> Phase 2 parallel gather -> Phase 3 serial append. Reusable for any parallel-with-ordering scenario."
  - "MultiToolProvider: 1-turn tool provider for precise multi-tool_call testing."

requirements-completed: [TOOL-06]

# Metrics
duration: 8min
completed: 2026-03-29
---

# Phase 19 Plan 02: Parallel Tool Dispatch Summary

**asyncio.gather parallel tool dispatch in AgentKernel with outcome-list ordering, replacing serial for-loop**

## Performance

- **Duration:** 8 min
- **Started:** 2026-03-29T14:45:52Z
- **Completed:** 2026-03-29T14:54:18Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Replaced serial tool dispatch loop with 3-phase parallel pattern: serial guard+hook gate -> parallel asyncio.gather -> serial post-hook+append
- Outcome-list pattern preserves original tool_call ordering across all mixed states (blocked, skipped, executed)
- Fixed ToolCallingProvider index bug (index:0 for all -> index:i with enumerate) enabling proper multi-tool testing
- Added MultiToolProvider and 6 comprehensive tests covering timing, ordering, error isolation, mixed states, single-tool regression, and closure exception handling

## Task Commits

Each task was committed atomically:

1. **Task 1: Fix ToolCallingProvider + add parallel dispatch tests (RED)** - `009e284` (test)
2. **Task 2: Implement parallel tool dispatch in AgentKernel._run_loop()** - `6398fc6` (feat)

## Files Created/Modified
- `matmaster/core/agent.py` - Replaced serial for-loop with 3-phase parallel dispatch (outcome-list + asyncio.gather)
- `tests/matmaster/core/test_agent.py` - Fixed ToolCallingProvider index, added MultiToolProvider, added TestParallelToolDispatch (6 tests)

## Decisions Made
- Used 4-tuple outcome list `(tc, tool_msg, tool_result, needs_post_hook)` to preserve ToolResult for post_hook without lossy reconstruction from message content
- Closure `_execute_tool()` catches all exceptions as primary error boundary; `gather(return_exceptions=True)` kept as defense-in-depth with isinstance(raw, BaseException) check
- post_tool_call hooks fire after all parallel tools complete, in original order (batch mode) -- accepted behavior change from per-tool immediate firing

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- 3 pre-existing integration test failures in `tests/matmaster/integration/` (test_bohrium_execution_contract, test_e2e_mat_master, test_quota_pipeline) caused by service layer bridge not yet updated for async MessageBus.emit(). These are Plan 01 scope (service layer bridge). All 1036 matmaster non-service-bridge tests pass.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Parallel tool dispatch ready for production use
- Service layer bridge (Plan 01) will resolve the 3 integration test failures
- Pattern established for any future parallel-with-ordering scenarios in the kernel

## Self-Check: PASSED

- All files exist (agent.py, test_agent.py, 19-02-SUMMARY.md)
- All commits found (009e284, 6398fc6)
- Key patterns verified: asyncio.gather(1), approved_indices(5), return_exceptions(1), TestParallelToolDispatch(1), MultiToolProvider(1)

---
*Phase: 19-tool-dispatch*
*Completed: 2026-03-29*
