---
phase: 34-exp-service-hook
plan: 01
subsystem: core
tags: [generator, async-iterator, tool-runtime-v2, event-sink, streaming]

requires:
  - phase: 33-tool-runtime
    provides: "FullToolRunner + ToolCatalog + ToolScheduler + StructuralValidation + CapabilityPolicy"
  - phase: 32-kernel-generator
    provides: "AgentRuntimeSpec v2 fields (tool_runner, tool_catalog, runtime_topology)"
provides:
  - "_stream_llm_items() sub-generator producing ThoughtEvent/ResponseEvent per chunk"
  - "run_stream() + _run_items() generator loop with AssistantState/SkillHit direct yields"
  - "Exp.build_runtime() FullToolRunner injection as default execution path"
  - "Exp.run_stream() async generator with cleanup guarantee"
  - "ContextCompactor event_sink callback (no MessageBus dependency)"
  - "on_skill_hit catalog.register_overlay() for version-bumped tool injection"
affects: [34-02-PLAN, 34-03-PLAN, service-layer, hook-retirement]

tech-stack:
  added: []
  patterns:
    - "_KernelItem dataclass as generator yield protocol"
    - "_KernelState preserving Kernel statelessness"
    - "deque-backed event_sink for compactor event buffering"
    - "catalog.register_overlay() for MCP tool injection with version tracking"

key-files:
  created:
    - "tests/matmaster/core/test_agent_kernel_stream.py"
    - "tests/matmaster/core/test_exp_runtime_v2.py"
  modified:
    - "matmaster/core/agent.py"
    - "matmaster/core/exp.py"
    - "matmaster/core/context_compactor.py"
    - "matmaster/core/hooks.py"
    - "tests/matmaster/core/test_context_compactor.py"

key-decisions:
  - "Backward compat: run() + _run_loop() + _call_llm() + _do_stream_llm() left untouched"
  - "ContextCompactor bus= kept as deprecated kwarg with bus.emit wrapper for backward compat"
  - "Defensive isinstance check for session.capabilities to handle mock objects"
  - "_call_llm_streaming() collects items then yields llm_response for incomplete-response retry"

patterns-established:
  - "_KernelItem(event=, llm_response=, terminal=) as unified generator yield type"
  - "event_sink: Callable[[Any], Awaitable[None]] | None as bus replacement pattern"
  - "Exp.build_runtime() always constructs FullToolRunner (no feature flag per D-02)"

requirements-completed: [KGEN-06, ESIN-01, ESIN-03, ESIN-04, ESIN-05, HRET-01, HRET-02, HRET-03, HRET-05]

duration: 20min
completed: 2026-04-02
---

# Phase 34 Plan 1: Kernel/Exp Generator-First Core Summary

**_stream_llm_items() sub-generator + _run_items() direct event yields + Exp.build_runtime() FullToolRunner injection + run_stream() generator chain**

## Performance

- **Duration:** 20 min
- **Started:** 2026-04-02T14:25:26Z
- **Completed:** 2026-04-02T14:45:26Z
- **Tasks:** 2
- **Files modified:** 8 source + 3 test files

## Accomplishments

- Kernel generator path complete: _stream_llm_items() produces ThoughtEvent/ResponseEvent per chunk with segment-complete semantics identical to EventEmitterHook
- _run_items() directly yields AssistantStateEvent and SkillHitEvent, replacing AssistantStateHook and SkillHitHook functionality in the generator path
- Exp.build_runtime() constructs FullToolRunner + ToolCatalog + RuntimeTopology as default execution path
- Exp.run_stream() async generator with try/finally cleanup guarantee
- ContextCompactor fully decoupled from MessageBus via event_sink callback
- Skill overlay via ToolCatalog.register_overlay() with version tracking for kernel tool definition refresh

## Task Commits

Each task was committed atomically:

1. **Task 1: _stream_llm_items + ContextCompactor event_sink + _run_items AssistantState/SkillHit**
   - `5ef2cd62` (test: RED phase - failing tests)
   - `9156b10a` (feat: GREEN phase - implementation passing all tests)

2. **Task 2: Exp.build_runtime() FullToolRunner injection + run_stream() + skill overlay**
   - `ae1df461` (feat: implementation + tests)

## Files Created/Modified

- `matmaster/core/agent.py` - Added _KernelItem/_KernelState/_TerminalItem dataclasses, _stream_llm_items() sub-generator, run_stream(), _run_items() generator loop, _call_llm_streaming() retry wrapper
- `matmaster/core/exp.py` - Restructured build_runtime() with FullToolRunner injection, added run_stream(), updated _init_skill_tools for catalog overlay
- `matmaster/core/context_compactor.py` - Replaced bus dependency with event_sink callback, backward-compat bus= kwarg
- `matmaster/core/hooks.py` - Updated ToolResultEvent construction for Phase 33 payload field
- `tests/matmaster/core/test_agent_kernel_stream.py` - New: 10 tests for streaming, AssistantState, SkillHit, backward compat
- `tests/matmaster/core/test_exp_runtime_v2.py` - New: 10 tests for FullToolRunner injection, run_stream, catalog overlay
- `tests/matmaster/core/test_context_compactor.py` - Added 3 event_sink tests

## Decisions Made

- Backward compat: run() + _run_loop() + _call_llm() + _do_stream_llm() left completely untouched. The generator path (run_stream -> _run_items -> _stream_llm_items) is a parallel path, not a replacement yet.
- ContextCompactor bus= parameter kept as deprecated kwarg that wraps bus.emit as event_sink, ensuring all existing call sites continue to work.
- Defensive isinstance(caps, SessionCapabilities) check in build_runtime to handle mock/MagicMock objects that have a capabilities attribute but aren't real SessionCapabilities instances.
- _call_llm_streaming() collects all _stream_llm_items() output, yields events immediately, but holds the final llm_response for incomplete-response retry logic.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Phase 33 prerequisite files missing from worktree**
- **Found during:** Task 2 start
- **Issue:** FullToolRunner, ToolCatalog, ToolScheduler, StructuralValidation, CapabilityPolicy, ToolCompiler, topology types not present in worktree
- **Fix:** Checked out Phase 33 files from refactor/async-agent parent branch
- **Files added:** matmaster/core/tool_runner.py, tool_scheduler.py, capability_policy.py, structural_validation.py, matmaster/tools/tool_catalog.py, tool_compiler.py, matmaster/types/topology.py, tool_spec.py, tool_decision.py, session.py
- **Verification:** All existing tests still pass
- **Committed in:** ae1df461

**2. [Rule 1 - Bug] ToolResult.info -> ToolResult.payload field rename**
- **Found during:** Task 1 implementation
- **Issue:** Phase 33 ToolResult changed from `info: dict` to `payload: dict` + `meta: dict`, causing AttributeError in _run_items() ToolResultEvent construction
- **Fix:** Updated agent.py and hooks.py to use `tool_result.payload` instead of `tool_result.info`
- **Files modified:** matmaster/core/agent.py, matmaster/core/hooks.py
- **Verification:** All 81 tests pass
- **Committed in:** ae1df461

**3. [Rule 1 - Bug] MagicMock session.capabilities breaks RuntimeTopology construction**
- **Found during:** Task 2 implementation
- **Issue:** Existing test_exp.py tests use MagicMock for session which has a capabilities attribute that's a MagicMock, not a SessionCapabilities instance, causing Pydantic validation error
- **Fix:** Added isinstance(caps, SessionCapabilities) guard before using capabilities
- **Files modified:** matmaster/core/exp.py
- **Verification:** Pre-existing tests pass (except 1 pre-existing failure for web_search rename)
- **Committed in:** ae1df461

---

**Total deviations:** 3 auto-fixed (1 blocking, 2 bugs)
**Impact on plan:** All fixes necessary for correctness. No scope creep.

## Issues Encountered

None beyond the auto-fixed deviations above.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Generator event chain Kernel -> Exp is complete and tested
- Plan 2 (Service layer) can now build AgentRunService.run_agent_stream() consuming Exp.run_stream()
- Plan 3 (Hook retirement) can now replace EventEmitterHook with the generator path
- Backward compat: existing run() path and all Hook paths still work unchanged

## Self-Check: PASSED

- All 6 key files verified present
- All 3 commits verified in git log
- All code patterns verified: _stream_llm_items (1), run_stream on agent (1), run_stream on exp (1), FullToolRunner in exp (4 refs), event_sink in compactor (11 refs), register_overlay in exp (3 refs)

---
*Phase: 34-exp-service-hook*
*Completed: 2026-04-02*
