---
phase: 34-exp-service-hook
plan: 04
subsystem: kernel
tags: [agent-kernel, tool-runner, bus-event, tool-catalog, generator, gap-closure]

requires:
  - phase: 34-01
    provides: "_run_items generator + run_stream + _stream_llm_items sub-generator"
  - phase: 34-02
    provides: "Exp.run_stream() + AgentRunService.run_agent_stream() integration"
  - phase: 34-03
    provides: "Hook retirement (3 hooks deleted, generator events replace them)"
provides:
  - "FullToolRunner activation as default execution path in _run_items()"
  - "run_stream() yields BusEvent objects (not _KernelItem) with RunResultEvent terminal"
  - "ToolCatalog version-aware cache invalidation in _run_items()"
affects: [tool-runtime-v2, exp-service-hook, bus-deprecation]

tech-stack:
  added: []
  patterns:
    - "run_stream() BusEvent yield contract: all items are BusEvent, terminal is RunResultEvent"
    - "FullToolRunner path bypasses legacy guard/hook gating (seven-step chain handles it)"
    - "Version-stamped tool definition caching via _KernelState.last_catalog_version"

key-files:
  created: []
  modified:
    - matmaster/core/agent.py
    - tests/matmaster/core/test_agent_kernel_stream.py
    - tests/matmaster/core/test_exp_runtime_v2.py

key-decisions:
  - "FullToolRunner path bypasses entire legacy guard/hook block, not just the execution step, because FullToolRunner has its own GuardPipeline + StructuralValidation + CapabilityPolicy chain"
  - "run_stream() consumes _KernelItem internally and yields only BusEvent via _consume_and_yield() inner generator"
  - "Catalog version check uses state.last_catalog_version = -1 initial sentinel, ensuring first-turn build_definitions() always fires"

patterns-established:
  - "run_stream() -> AsyncIterator[Any] (BusEvent union) contract: callers never see _KernelItem"
  - "ToolRunner conditional: if spec.tool_runner is not None: execute_batch path, else: legacy registry path"

requirements-completed: [ESIN-01, ESIN-02, ESIN-04, ESIN-05]

duration: 10min
completed: 2026-04-03
---

# Phase 34 Plan 4: Gap Closure Summary

**FullToolRunner activated as default execution path + run_stream() yields BusEvent + ToolCatalog version-aware cache invalidation**

## Performance

- **Duration:** 10 min
- **Started:** 2026-04-02T15:52:17Z
- **Completed:** 2026-04-02T16:02:33Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 3

## Accomplishments
- FullToolRunner.execute_batch() is now the active tool execution path when spec.tool_runner is set, bypassing legacy guard/hook gating entirely
- run_stream() yields BusEvent objects directly (not _KernelItem), with RunResultEvent as the terminal event, completing the generator event chain
- ToolCatalog version changes (from register_overlay) trigger automatic tool_definitions cache invalidation and rebuild
- All 72 kernel/stream/service/exp tests pass (6 new gap tests + existing tests updated for BusEvent contract)

## Task Commits

Each task was committed atomically (TDD cycle):

1. **Task 1 (RED): Failing tests for 3 gaps** - `3800e956` (test)
2. **Task 1 (GREEN): Close 3 gaps + pass all tests** - `e0f4ce35` (feat)

## Files Created/Modified
- `matmaster/core/agent.py` - Gap 1: FullToolRunner conditional in _run_items(); Gap 2: run_stream() BusEvent yield with _consume_and_yield(); Gap 3: version-aware tool_defs caching
- `tests/matmaster/core/test_agent_kernel_stream.py` - 6 new gap closure tests + updated existing tests for BusEvent contract
- `tests/matmaster/core/test_exp_runtime_v2.py` - Updated test_run_stream_yields_events for BusEvent contract

## Decisions Made
- FullToolRunner path bypasses the entire legacy guard+pre_hook+execute+post_hook block, not just the parallel execution step. This is because FullToolRunner's seven-step chain (Catalog -> StructuralValidation -> GuardPipeline -> CapabilityPolicy -> Scheduler -> Execute -> Release) handles all of these concerns internally.
- run_stream() uses an inner async generator `_consume_and_yield()` to consume _KernelItem and yield BusEvent, keeping the transformation logic clean and contained.
- Terminal _KernelItem conversion maps reason='cancelled' to status='cancelled', reason='invalid_finish' to status='failed', everything else to status='completed'.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated existing stream/exp tests for BusEvent contract**
- **Found during:** Task 1 (GREEN phase)
- **Issue:** Existing tests in TestRunItemsAssistantState, TestRunItemsSkillHit, and TestRunStream referenced _KernelItem.event which no longer exists in run_stream() output
- **Fix:** Updated tests to check BusEvent objects directly (isinstance checks, attribute access without .event wrapper)
- **Files modified:** tests/matmaster/core/test_agent_kernel_stream.py, tests/matmaster/core/test_exp_runtime_v2.py
- **Verification:** All 72 tests pass
- **Committed in:** e0f4ce35

---

**Total deviations:** 1 auto-fixed (Rule 1 - test adaptation for new contract)
**Impact on plan:** Necessary adaptation. The plan noted "All existing kernel run() tests pass unchanged" for the run() path (which remains unchanged), but run_stream() consumers needed updating for the BusEvent contract. No scope creep.

## Issues Encountered
None - implementation followed the plan's revised approach cleanly.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 34 goal "FullToolRunner activated as default execution path" is now achieved
- Phase 34 goal "Generator event stream spans full chain" is now achieved
- All 3 verification gaps from 34-VERIFICATION.md are closed
- Ready for constraint migration (read-before-modify -> RunStateGuard, bash dangerous commands -> CapabilityPolicy)

## Self-Check: PASSED

- All 4 key files FOUND
- Both commit hashes FOUND (3800e956, e0f4ce35)
- 72/72 tests pass (kernel + stream + service + exp)
- All acceptance criteria verified via grep

---
*Phase: 34-exp-service-hook*
*Completed: 2026-04-03*
