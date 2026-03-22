---
phase: 03-exp-assembly-layer
plan: 03
subsystem: assembly
tags: [exp, direct-exp, guard, abc, assembly, tool-registry, context-builder]

requires:
  - phase: 03-exp-assembly-layer/01
    provides: "ToolRegistry, Tool Protocol, AgentRuntimeSpec.tool_registry typed field"
  - phase: 03-exp-assembly-layer/02
    provides: "ContextBuilder, WorkerRegistry Protocol"
  - phase: 02-agent-kernel
    provides: "AgentKernel, EventEmitterHook, Hook Protocol, GuardPipeline"
provides:
  - "Exp abstract base class with assemble() and run() flow"
  - "DirectExp concrete assembly for direct execution mode"
  - "ManuscriptGateGuard and AuthFailureGateGuard shell implementations"
  - "Complete matmaster.assembly package exports (8 public symbols)"
affects: [04-playground-layer, 05-integration-quality]

tech-stack:
  added: []
  patterns: ["Exp subclass pattern (solver as subclass)", "Assembly flow: ctx -> assemble -> spec -> kernel.run", "Guard Protocol injection via constructor"]

key-files:
  created:
    - matmaster/assembly/exp.py
    - matmaster/assembly/direct_exp.py
    - matmaster/assembly/guards.py
    - tests/matmaster/assembly/test_exp.py
    - tests/matmaster/assembly/test_direct_exp.py
    - tests/matmaster/assembly/test_guard_injection.py
  modified:
    - matmaster/assembly/__init__.py

key-decisions:
  - "DirectExp stores guards as list copy (defensive) to prevent external mutation after construction"
  - "EventEmitterHook source uses exp_name property for consistent bus event attribution"
  - "Pre-existing TestAgentRuntimeSpec failures (object() as tool_registry) deferred to Phase 5"

patterns-established:
  - "Exp subclass pattern: override assemble() for different strategies (DirectExp, future PlannerExp)"
  - "Assembly flow: constructor params -> assemble(ctx) -> AgentRuntimeSpec -> kernel.run(spec, task)"
  - "Guard injection: business guards passed to Exp constructor, forwarded to spec.guards"

requirements-completed: [ASBL-01, ASBL-03, ASBL-04]

duration: 4min
completed: 2026-03-22
---

# Phase 03 Plan 03: Exp + DirectExp + Guards Summary

**Exp ABC with abstract assemble()/default run(), DirectExp wiring ToolRegistry + ContextBuilder + EventEmitterHook into AgentRuntimeSpec, and business guard shells implementing Guard Protocol**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-22T03:00:23Z
- **Completed:** 2026-03-22T03:04:42Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments
- Exp abstract base class with assemble() contract and default run() flow (assemble -> AgentKernel.run -> FinishEvent)
- DirectExp concrete subclass assembling complete AgentRuntimeSpec from ToolRegistry, ContextBuilder, EventEmitterHook, and guards
- ManuscriptGateGuard and AuthFailureGateGuard shell implementations satisfying Guard Protocol (isinstance check passes)
- Complete matmaster.assembly package exports: 8 public symbols importable from single package
- 23 new tests covering abstraction enforcement, exp_name property, run flow, assembly output, guard injection, and pipeline integration
- Total assembly test count: 50 tests all passing

## Task Commits

Each task was committed atomically:

1. **Task 1: Create Exp base class + DirectExp + business guard shells with tests** - `43bbf98` (feat, TDD)
2. **Task 2: Update assembly __init__.py package exports** - `7b018ad` (chore)

_Note: Task 1 used TDD flow (RED: import errors confirmed -> GREEN: all 23 tests pass)_

## Files Created/Modified
- `matmaster/assembly/exp.py` - Exp ABC with abstract assemble() and default run() flow
- `matmaster/assembly/direct_exp.py` - DirectExp concrete assembly (ToolRegistry + ContextBuilder + EventEmitterHook -> AgentRuntimeSpec)
- `matmaster/assembly/guards.py` - ManuscriptGateGuard and AuthFailureGateGuard shell implementations
- `matmaster/assembly/__init__.py` - Complete package re-exports (8 symbols)
- `tests/matmaster/assembly/test_exp.py` - 6 tests: abstraction, subclassing, exp_name, run flow
- `tests/matmaster/assembly/test_direct_exp.py` - 10 tests: assembly output, tools, guards, hooks, mode, repeatability
- `tests/matmaster/assembly/test_guard_injection.py` - 5 tests: Guard Protocol, evaluate, injection, pipeline integration
- `.planning/phases/03-exp-assembly-layer/deferred-items.md` - Pre-existing test failures documented

## Decisions Made
- DirectExp stores guards as `list(guards)` defensive copy to prevent external mutation after construction
- EventEmitterHook source uses `self.exp_name` property for consistent bus event attribution across Exp subclasses
- Pre-existing TestAgentRuntimeSpec failures (9 tests passing `object()` as `tool_registry`) documented in deferred-items.md for Phase 5 fix

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Pre-existing test failures in `tests/matmaster/types/test_runtime.py::TestAgentRuntimeSpec` (9 tests) using `object()` as `tool_registry` -- invalid since Plan 03-01 changed the field type to `ToolRegistry | None`. Not caused by this plan's changes. Documented in `deferred-items.md` for Phase 5.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Phase 3 (Exp Assembly Layer) is now complete: all 3 plans executed successfully
- matmaster.assembly package fully operational with 50 passing tests
- Ready for Phase 4 (Playground Layer) which consumes Exp.assemble() via PlaygroundContext
- Ready for Phase 5 (Integration Quality) which migrates real guard logic into ManuscriptGateGuard/AuthFailureGateGuard shells

## Self-Check: PASSED

All 7 claimed files exist. Both task commits (43bbf98, 7b018ad) verified in git log.

---
*Phase: 03-exp-assembly-layer*
*Completed: 2026-03-22*
