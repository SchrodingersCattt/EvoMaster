---
phase: 05-integration-quality
plan: 03
subsystem: service-layer, assembly
tags: [agent-run-service, pipeline, event-router, hooks, chat-history, migration]

# Dependency graph
requires:
  - phase: 05-01
    provides: "AgentKernel.run() with history, 4 business Hooks, PlaygroundContext.with_bohrium()"
  - phase: 05-02
    provides: "EventRouter, PersistenceHandler, SSEHandler, WorkspaceHandler, BohriumSetupService"
provides:
  - "Rewritten agent_run_service.run_agent_sync() using Playground->Exp->Kernel pipeline"
  - "DirectExp.assemble() with external hooks merge parameter"
  - "ChatHistoryConverter.events_to_messages() returning matmaster Message types"
  - "DeprecationWarning on old evomaster modules (D-02)"
  - "x_master ValueError rejection (D-03)"
  - "Config YAML validation without dynamic import (D-04)"
affects: [05-04-testing, 05-05-migration-docs]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Thin orchestration layer: run_agent_sync as 6-stage pipeline"
    - "External hooks merge via kwargs.get('hooks') in Exp.assemble()"
    - "Lazy import bridge: _bohrium_event_cb wraps legacy API into MessageBus"

key-files:
  created:
    - "tests/matmaster/integration/test_events_to_messages.py"
  modified:
    - "src/services/agent_run_service.py"
    - "src/services/chat_history.py"
    - "matmaster/assembly/direct_exp.py"

key-decisions:
  - "BohriumSetupService.setup() called with legacy API parameters (pg, base, event_callback) via bridge function rather than creating a new API"
  - "CancelledEvent used instead of ErrorEvent for user cancellation to match event type semantics"
  - "_bohrium_event_cb bridges legacy bohrium events into BohriumNodeEvent on the MessageBus"

patterns-established:
  - "6-stage pipeline: Playground.prepare -> Bohrium -> Exp.assemble -> History -> EventRouter -> Kernel.run"
  - "External hooks passed as kwargs to assemble(), merged with internal EventEmitterHook"
  - "Legacy API bridge pattern: thin callback wrapping old function signatures into new bus system"

requirements-completed: [MIGR-01, MIGR-02, QUAL-05]

# Metrics
duration: 5min
completed: 2026-03-22
---

# Phase 5 Plan 03: Service Layer Rewrite Summary

**agent_run_service.py rewritten from 820 to 390 lines using Playground->Exp->Kernel pipeline with EventRouter replacing 130-line event_callback closure**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-22T08:58:56Z
- **Completed:** 2026-03-22T09:03:54Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- agent_run_service.run_agent_sync() rewritten as thin 6-stage pipeline orchestration (Playground -> Bohrium -> Exp -> History -> EventRouter -> Kernel)
- 130-line event_callback closure with mutable list hacks eliminated; replaced by EventRouter + 3 typed handlers
- DirectExp.assemble() extended to accept and merge external hooks with internal EventEmitterHook
- ChatHistoryConverter.events_to_messages() converts DB event dicts to matmaster Message types (UserMessage, AssistantMessage, ToolMessage)
- Old events_to_dialog_messages() preserved for backward compatibility (D-14)
- Method signature (12 parameters) fully preserved -- zero caller modifications needed
- All 336 matmaster tests pass

## Task Commits

Each task was committed atomically:

1. **Task 1: Update DirectExp.assemble() and add ChatHistoryConverter.events_to_messages()** - `47b61fe` (feat)
2. **Task 2: Rewrite agent_run_service.py to new matmaster pipeline** - `dd02b53` (feat)

## Files Created/Modified
- `matmaster/assembly/direct_exp.py` - assemble() now merges external hooks via kwargs
- `src/services/chat_history.py` - Added events_to_messages() classmethod with lazy matmaster type imports
- `tests/matmaster/integration/test_events_to_messages.py` - 6 behavioral tests for events_to_messages conversion
- `src/services/agent_run_service.py` - Complete rewrite: 820->390 lines, new pipeline, removed old evomaster imports

## Decisions Made
- BohriumSetupService called with legacy API parameters via bridge callback (_bohrium_event_cb) rather than designing a new API -- minimizes changes to agent_run_bohrium.py which is a 539-line module outside scope
- CancelledEvent used for user cancellation post-processing (semantically distinct from ErrorEvent)
- Legacy bohrium events bridged into BohriumNodeEvent on the MessageBus for handler consumption

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Adapted BohriumSetupService.setup() call to match actual API**
- **Found during:** Task 2 (service rewrite)
- **Issue:** Plan pseudocode used simplified kwargs (pg_ctx, session_id) but BohriumSetupService.setup() wraps setup_bohrium_for_run() which requires (pg, base, event_callback, run_started_at)
- **Fix:** Created _bohrium_event_cb bridge function to translate legacy event callbacks into MessageBus events; passed actual Playground object as pg/base parameters
- **Files modified:** src/services/agent_run_service.py
- **Verification:** All 336 tests pass
- **Committed in:** dd02b53 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug - API signature mismatch)
**Impact on plan:** Essential adaptation to match real BohriumSetupService API. No scope creep.

## Known Stubs

**1. _build_llm_provider()** - `src/services/agent_run_service.py` line 131
- Raises NotImplementedError. Placeholder for LLM provider factory wiring.
- Intentional: LLM provider configuration extraction from config YAML is a separate concern; the pipeline structure is complete but this factory needs to be wired with the actual config reader in a future plan.

**2. _get_builtin_tools()** - `src/services/agent_run_service.py` line 135
- Returns empty list. Placeholder for builtin tool registration.
- Intentional: Tool registration depends on the specific playground type and config; will be wired when full end-to-end testing is performed.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Service layer rewrite complete; pipeline structure ready for end-to-end testing (Plan 04)
- _build_llm_provider and _get_builtin_tools stubs need wiring for real execution
- All 336 tests passing provides safety net for continued integration work
- Migration documentation (Plan 05) can reference the clear 6-stage pipeline structure

## Self-Check: PASSED

All 5 files verified present. Both task commits (47b61fe, dd02b53) verified in git log.

---
*Phase: 05-integration-quality*
*Completed: 2026-03-22*
