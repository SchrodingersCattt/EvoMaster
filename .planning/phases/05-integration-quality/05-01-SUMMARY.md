---
phase: 05-integration-quality
plan: 01
subsystem: agent-kernel, hooks
tags: [hooks, BaseHook, MessageBus, frozen-model, multi-turn, TDD]

# Dependency graph
requires:
  - phase: 02-agent-kernel
    provides: "AgentKernel.run(), Hook Protocol, BaseHook, EventEmitterHook"
  - phase: 01-foundation-contracts
    provides: "MessageBus, BusEvent types, PlaygroundContext frozen model"
provides:
  - "AgentKernel.run() with history: list[Message] | None parameter"
  - "PlaygroundContext.with_bohrium() method for frozen post-construction update"
  - "Exp.run() with history pass-through to kernel"
  - "ConfirmationHook -- blocks tool execution pending user confirmation"
  - "OutputProcessorHook -- emits auto_save/summarize events via pattern matching"
  - "SkillHitHook -- emits SkillHitEvent for skill: prefixed tools"
  - "AssistantStateHook -- emits AssistantStateEvent for tool-calling assistant turns"
  - "matmaster/hooks/ package with __all__ exports"
affects: [05-02, 05-03, 05-04]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Business hooks inherit BaseHook and override only needed hook points"
    - "ReplyQueueLike Protocol duplicated in hooks/confirmation.py to avoid cross-layer import"
    - "Frozen model update via model_copy(update=...) for post-construction data"

key-files:
  created:
    - "matmaster/hooks/__init__.py"
    - "matmaster/hooks/confirmation.py"
    - "matmaster/hooks/output_processor.py"
    - "matmaster/hooks/skill_hit.py"
    - "matmaster/hooks/assistant_state.py"
    - "tests/matmaster/hooks/__init__.py"
    - "tests/matmaster/hooks/test_confirmation.py"
    - "tests/matmaster/hooks/test_output_processor.py"
    - "tests/matmaster/hooks/test_skill_hit.py"
    - "tests/matmaster/hooks/test_assistant_state.py"
  modified:
    - "matmaster/engine/agent.py"
    - "matmaster/types/context.py"
    - "matmaster/assembly/exp.py"
    - "tests/matmaster/engine/test_agent.py"
    - "tests/matmaster/types/test_context.py"

key-decisions:
  - "ReplyQueueLike Protocol duplicated in hooks/confirmation.py to avoid importing from src/services/"
  - "AssistantStateHook only emits when last AssistantMessage has tool_calls (not for plain text responses)"
  - "OutputProcessorHook uses substring matching (same logic as existing auto_save_tool_output_patterns)"
  - "SkillHitHook extracts skill_name by stripping 'skill:' prefix from tool_call.name"

patterns-established:
  - "Business hooks in matmaster/hooks/ package, one file per hook"
  - "EventEmitterHook stays in engine/hooks.py as generic bridge"
  - "Frozen model update pattern: model_copy(update={...}) for post-construction data"

requirements-completed: [MIGR-01, MIGR-02]

# Metrics
duration: 4min
completed: 2026-03-22
---

# Phase 5 Plan 01: Foundation Components Summary

**Multi-turn history support in AgentKernel, frozen-model with_bohrium() update, and 4 business Hooks (Confirmation, OutputProcessor, SkillHit, AssistantState) per D-07/D-08**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-22T08:48:47Z
- **Completed:** 2026-03-22T08:53:19Z
- **Tasks:** 2
- **Files modified:** 15

## Accomplishments
- AgentKernel.run() extended with `history: list[Message] | None` parameter for multi-turn conversation support
- PlaygroundContext.with_bohrium() enables frozen model post-construction update for Bohrium SSH results
- Exp.run() passes through history to kernel for complete pipeline support
- All 4 business hooks implemented in matmaster/hooks/ package with full test coverage (15 tests)
- 47 total tests pass across all plan files

## Task Commits

Each task was committed atomically:

1. **Task 1: Extend AgentKernel.run() with history param and add PlaygroundContext.with_bohrium()** - `f06f74f` (feat)
2. **Task 2: Implement 4 business Hooks in matmaster/hooks/ package** - `13c573a` (feat)

## Files Created/Modified
- `matmaster/engine/agent.py` - Added history parameter to run(), messages constructed as [System, *history, User(task)]
- `matmaster/types/context.py` - Added with_bohrium() method to PlaygroundContext frozen model
- `matmaster/assembly/exp.py` - Added history parameter pass-through to run() and kernel.run()
- `matmaster/hooks/__init__.py` - Package init exporting all 4 business hooks
- `matmaster/hooks/confirmation.py` - ConfirmationHook with ReplyQueueLike blocking and timeout
- `matmaster/hooks/output_processor.py` - OutputProcessorHook with auto_save/summarize pattern matching
- `matmaster/hooks/skill_hit.py` - SkillHitHook detecting skill: prefix and emitting SkillHitEvent
- `matmaster/hooks/assistant_state.py` - AssistantStateHook emitting state for tool-calling assistant turns
- `tests/matmaster/engine/test_agent.py` - 3 new tests for history parameter behavior
- `tests/matmaster/types/test_context.py` - 3 new tests for with_bohrium() immutability
- `tests/matmaster/hooks/test_confirmation.py` - 5 tests covering all confirmation paths
- `tests/matmaster/hooks/test_output_processor.py` - 4 tests for pattern matching and no-match cases
- `tests/matmaster/hooks/test_skill_hit.py` - 3 tests for skill prefix detection
- `tests/matmaster/hooks/test_assistant_state.py` - 3 tests for assistant state emission logic

## Decisions Made
- ReplyQueueLike Protocol duplicated in hooks/confirmation.py rather than importing from src/services/agent_run_service.py -- keeps matmaster package independent of service layer
- AssistantStateHook only emits when last AssistantMessage has tool_calls -- plain text responses don't need state persistence for downstream handlers
- OutputProcessorHook uses substring matching (any() with 'in' operator) consistent with existing auto_save_tool_output_patterns logic
- SkillHitHook uses simple startswith('skill:') check, extracts skill_name by stripping prefix

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Known Stubs

None -- all hooks are fully implemented with real business logic, no placeholder data.

## Next Phase Readiness
- AgentKernel history support ready for Plan 02 (EventRouter) and Plan 03 (service rewrite)
- All 4 business hooks ready for injection into AgentRuntimeSpec.hooks list
- PlaygroundContext.with_bohrium() ready for Bohrium SSH integration in service layer

## Self-Check: PASSED

All 9 created files verified present. Both task commits (f06f74f, 13c573a) verified in git log.

---
*Phase: 05-integration-quality*
*Completed: 2026-03-22*
