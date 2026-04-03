---
phase: 33-toolrunner-toolscheduler
plan: 03
subsystem: tools
tags: [tool-runner, tool-scheduler, resource-claims, capability-policy, structural-validation, fast-path]

requires:
  - phase: 33-01
    provides: StructuralValidation (Layer A) + CapabilityPolicy Protocol + DefaultCapabilityPolicy (Layer C)
  - phase: 33-02
    provides: ToolScheduler with _RWLock + Semaphore scheduling + SchedulerTicket
provides:
  - FullToolRunner class with complete seven-step execution chain
  - BUILTIN_CLAIMS lookup table for 16 builtin tools
  - BUILTIN_META lookup table for ToolPlane + effect_level + fast_path_eligible
  - ToolCatalog.get_tool() enhanced with claims/meta injection
affects: [phase-34-exp-integration, phase-35-tool-registry-degradation]

tech-stack:
  added: []
  patterns:
    - "Seven-step execution chain: Catalog -> Validation -> Guard -> Policy -> FastPath -> Scheduler -> Execute -> Release"
    - "meta['layer'] error attribution pattern for multi-layer tool pipelines"
    - "BUILTIN_CLAIMS/BUILTIN_META lookup tables for builtin tool metadata injection"
    - "Fast path bypass: effect_level='none' + shared_read + fast_path_eligible skips Scheduler"

key-files:
  created: [tests/matmaster/core/test_full_tool_runner.py, tests/matmaster/core/test_builtin_claims.py]
  modified: [matmaster/core/tool_runner.py, matmaster/tools/tool_catalog.py]

key-decisions:
  - "mm_web_search used instead of web_search (plan referenced wrong name; actual tool is mm_web_search after Phase 26 rename)"
  - "FullToolRunner does not call pre_hook/post_hook per D-01, cleanly separating from InlineToolRunner"
  - "Scheduler.release() awaited in finally block to prevent resource leaks from executor exceptions"

patterns-established:
  - "Layer error attribution: every deny/error in the execution chain includes meta={'layer': '<source>'} for debugging"
  - "Fast path condition: three-way AND (effect_level=='none', all claims shared_read, fast_path_eligible)"
  - "Builtin tool metadata injection via module-level lookup dicts rather than tool-class modification"

requirements-completed: [TRUN-03, D-09]

duration: 5min
completed: 2026-04-02
---

# Phase 33 Plan 03: FullToolRunner + Builtin Claims Summary

**FullToolRunner seven-step execution chain (Catalog->Validation->Guard->Policy->Scheduler->Execute->Release) with BUILTIN_CLAIMS/BUILTIN_META lookup tables for 16 builtin tools**

## Performance

- **Duration:** 5 min
- **Started:** 2026-04-02T11:51:14Z
- **Completed:** 2026-04-02T11:56:55Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- FullToolRunner implements the complete seven-step execution chain per D-05, with every layer producing meta["layer"]-attributed ToolResult on deny/error
- BUILTIN_CLAIMS and BUILTIN_META inject correct ResourceClaim, ToolPlane, effect_level, and fast_path_eligible for all 16 builtin tools
- Fast path condition correctly bypasses Scheduler for read-only tools while still enforcing CapabilityPolicy
- Cancel semantics, executor exception handling (with await release), and on_result callbacks all verified
- 41 tests passing (13 FullToolRunner + 15 builtin claims + 13 InlineToolRunner regression)

## Task Commits

Each task was committed atomically:

1. **Task 1: FullToolRunner + Tests (TDD RED)** - `4fe8c089` (test)
2. **Task 1: FullToolRunner + Tests (TDD GREEN)** - `8b0d3cbd` (feat)
3. **Task 2: Builtin Claims + ToolCatalog (TDD RED)** - `8249dc75` (test)
4. **Task 2: Builtin Claims + ToolCatalog (TDD GREEN)** - `118ac4ee` (feat)

## Files Created/Modified
- `matmaster/core/tool_runner.py` - Added FullToolRunner class (324 lines total, 160 new)
- `matmaster/tools/tool_catalog.py` - Added BUILTIN_CLAIMS, BUILTIN_META dicts + enhanced get_tool()
- `tests/matmaster/core/test_full_tool_runner.py` - 13 tests across 9 test classes for execution chain
- `tests/matmaster/core/test_builtin_claims.py` - 15 tests for builtin tool metadata declarations

## Decisions Made
- Used `mm_web_search` instead of `web_search` as the plan referenced the pre-Phase-26 name. The actual tool was renamed in Phase 26 to `mm_web_search`
- FullToolRunner does not call pre_hook/post_hook (per D-01), cleanly separating execution concerns from InlineToolRunner
- Scheduler.release() is awaited in the finally block -- critical because _RWLock.release_write/release_read use `async with self._lock`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed web_search tool name to mm_web_search**
- **Found during:** Task 2 (Builtin Claims)
- **Issue:** Plan referenced `web_search` but the actual tool name is `mm_web_search` after Phase 26 rename
- **Fix:** Used `mm_web_search` in BUILTIN_CLAIMS and BUILTIN_META
- **Files modified:** matmaster/tools/tool_catalog.py, tests/matmaster/core/test_builtin_claims.py
- **Verification:** All tests pass with correct tool name
- **Committed in:** 118ac4ee (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Trivial name fix. No scope change.

## Issues Encountered
None

## Known Stubs
None -- all data paths are fully wired.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- FullToolRunner is ready for integration into Exp.build_runtime() in Phase 34 (ESIN-04)
- All three Plan 01-03 modules are complete and tested independently
- Phase 34 will wire FullToolRunner into the actual execution path via Exp + AgentKernel

## Self-Check: PASSED

- All 4 created/modified files exist on disk
- All 4 commit hashes found in git log
- Line counts meet min_lines thresholds (tool_runner.py 324>=200, tool_catalog.py 147>=80, test_full_tool_runner.py 519>=120, test_builtin_claims.py 214>=40)
- 41 tests passing (13+15+13)

---
*Phase: 33-toolrunner-toolscheduler*
*Completed: 2026-04-02*
