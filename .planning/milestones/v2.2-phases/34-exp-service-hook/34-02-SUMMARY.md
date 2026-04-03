---
phase: 34-exp-service-hook
plan: 02
subsystem: api
tags: [generator, event-stream, bus-bridge, source-normalization, sse]

# Dependency graph
requires:
  - phase: 34-01
    provides: "Exp.run_stream() async generator, _stream_llm_items() sub-generator, ToolResultEvent.info field"
provides:
  - "AgentRunService.run_agent_stream() -- generator event bridge from Exp to bus/EventRouter"
  - "Source normalization (ESIN-06) on generator events"
  - "ToolResult.payload/info -> SSE info dual-key mapping (ESIN-07)"
  - "StreamClosedEvent emission after terminal event"
affects: [34-03, hook-retirement, sse-frontend]

# Tech tracking
tech-stack:
  added: [contextlib.aclosing]
  patterns: [generator-event-bridge, source-normalization-at-service-boundary]

key-files:
  created:
    - tests/matmaster/services/__init__.py
    - tests/matmaster/services/test_agent_run_stream.py
  modified:
    - src/services/agent_run_service.py
    - matmaster/integration/event_payloads.py
    - tests/matmaster/integration/test_event_payloads.py

key-decisions:
  - "event_payloads.py reads 'info' first then 'payload' for backward compat with both generator and Hook paths"
  - "run_agent_stream() duplicates Stages 1-5 inline from run_agent() rather than extracting shared setup (per REGR-02)"
  - "Generator path omits AssistantStateHook/SkillHitHook/OutputProcessorHook (replaced by generator events)"

patterns-established:
  - "Source normalization at service boundary: _normalize_public_source() called on each event before bus.emit_nowait()"
  - "aclosing() wraps async generator for guaranteed cleanup on all exit paths"

requirements-completed: [ESIN-02, ESIN-06, ESIN-07, REGR-02]

# Metrics
duration: 10min
completed: 2026-04-02
---

# Phase 34 Plan 2: Service Layer Stream Bridge Summary

**AgentRunService.run_agent_stream() consumes Exp.run_stream() generator, bridges events to bus/EventRouter with source normalization and StreamClosedEvent lifecycle**

## Performance

- **Duration:** 10 min
- **Started:** 2026-04-02T14:54:36Z
- **Completed:** 2026-04-02T15:05:00Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Implemented run_agent_stream() on AgentRunService: full pipeline Stages 1-5 + generator event bridge at Stage 6
- Source normalization (ESIN-06) ensures all generator events have MatMaster/MatMaster:<exp> source labels before reaching bus
- Fixed ToolResult.payload -> SSE info mapping to support both 'info' key (new generator path) and 'payload' key (legacy Hook path)
- StreamClosedEvent/CancelledEvent lifecycle matches run_agent() semantics exactly
- run_agent() completely untouched (REGR-02 regression safety)

## Task Commits

Each task was committed atomically:

1. **Task 1: run_agent_stream() + source normalization + integration tests**
   - `c2d43de4` (test): add failing tests for run_agent_stream (TDD RED)
   - `8b74d020` (feat): implement run_agent_stream with generator event bridge (TDD GREEN)

2. **Task 2: ToolResult.payload -> SSE info mapping verification**
   - `876cf918` (test): add failing tests for ToolResult payload->info mapping (TDD RED)
   - `c155fd62` (feat): fix ToolResult info mapping for generator event path (TDD GREEN)

## Files Created/Modified

- `src/services/agent_run_service.py` -- Added run_agent_stream() method (280 lines), aclosing import, _normalize_public_source import, RunResultEvent import
- `matmaster/integration/event_payloads.py` -- Fixed tool_result info mapping: `payload.get('info') or payload.get('payload') or {}`
- `tests/matmaster/services/__init__.py` -- New test package
- `tests/matmaster/services/test_agent_run_stream.py` -- 7 integration tests for event bridging, source normalization, lifecycle events, error handling
- `tests/matmaster/integration/test_event_payloads.py` -- 8 new tests for ESIN-07 payload->info mapping and ESIN-06 source normalization

## Decisions Made

- **Dual-key info mapping**: `event_payloads.py` reads `'info'` first (from ToolResultEvent.model_dump), falls back to `'payload'` (legacy Hook path). This maintains backward compatibility while supporting the new generator events.
- **Stage duplication over extraction**: run_agent_stream() duplicates Stages 1-5 from run_agent() rather than extracting a shared setup method. This follows REGR-02 (don't modify run_agent) and avoids premature abstraction before the old path is retired.
- **Generator path omits observer Hooks**: AssistantStateHook, SkillHitHook, OutputProcessorHook are not included in the generator path since their functionality is replaced by generator-yielded events. Only ConfirmationHook is conditionally included.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed ToolResult info mapping for generator event path**
- **Found during:** Task 2 (event_payloads verification)
- **Issue:** `_public_content_for_event` for `tool_result` reads `payload.get('payload')` but `ToolResultEvent.model_dump()` produces `'info'` key (not `'payload'`). Generator path events would always produce empty `info: {}` in SSE output.
- **Fix:** Changed to `payload.get('info') or payload.get('payload') or {}` to support both model_dump (new) and legacy dict (old) paths.
- **Files modified:** `matmaster/integration/event_payloads.py`
- **Verification:** `test_tool_result_info_key_from_model_dump` passes
- **Committed in:** c155fd62

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Bug fix was necessary for generator events to produce correct SSE output. The plan hinted at this gap ("No code changes needed UNLESS the existing mapping has gaps") and the gap existed.

## Issues Encountered

- Test patching challenge: `Exp`, `load_exp_config`, `load_llm_config`, `build_provider` are lazy imports inside method bodies, requiring patches at their source modules rather than at the consumer module namespace. Resolved by patching at `matmaster.core.exp.Exp`, `matmaster.config.loader.load_exp_config`, etc.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- run_agent_stream() is ready for Plan 3 to wire into API/Worker endpoints
- Generator event chain is complete: Kernel._run_items() -> Kernel.run_stream() -> Exp.run_stream() -> AgentRunService.run_agent_stream() -> bus.emit_nowait() -> EventRouter -> SSE/Persistence
- Plan 3 will handle Hook retirement and API switchover

## Self-Check: PASSED

- All 6 created/modified files exist on disk
- All 4 task commits found in git history (c2d43de4, 8b74d020, 876cf918, c155fd62)
- 7/7 run_agent_stream integration tests pass
- 18/18 event_payloads tests pass
- 39/39 kernel regression tests pass
- 1469/1473 full matmaster suite tests pass (4 pre-existing failures unrelated to this plan)

---
*Phase: 34-exp-service-hook*
*Completed: 2026-04-02*
