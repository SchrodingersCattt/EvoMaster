---
phase: 05-integration-quality
plan: 02
subsystem: integration
tags: [event-router, threading, async, workspace, bohrium, protocol]

# Dependency graph
requires:
  - phase: 01-foundation-contracts
    provides: BusEvent types, MessageBus queue
  - phase: 04-playground-layer
    provides: PlaygroundContext, WorkspaceArchivalConfig
provides:
  - EventRouter with background thread consumption and multi-handler dispatch
  - PersistenceHandler with migrated _should_persist_event filter rules
  - SSEHandler with migrated _should_skip_push filter rules and async/sync dispatch
  - WorkspaceHandler with debounced snapshot/upload on ToolResultEvent
  - BohriumSetupService wrapping agent_run_bohrium 4 functions
  - EventHandler Protocol for handler interface
affects: [05-03-service-rewrite, 05-04-testing]

# Tech tracking
tech-stack:
  added: []
  patterns: [single-consumer-multi-handler, protocol-based-handler, dependency-injection-for-upload]

key-files:
  created:
    - matmaster/integration/__init__.py
    - matmaster/integration/event_router.py
    - matmaster/integration/workspace_handler.py
    - matmaster/integration/bohrium_setup.py
    - tests/matmaster/integration/__init__.py
    - tests/matmaster/integration/test_event_router.py
    - tests/matmaster/integration/test_workspace_handler.py
  modified: []

key-decisions:
  - "PersistenceHandler._should_persist_type() exposed as method for type-level filter testing (log_line/llm_token not in BusEvent union)"
  - "SSEHandler detects async send_cb via asyncio.iscoroutinefunction at construction time, not per-call"
  - "WorkspaceHandler uses injected snapshot_fn and upload_fn for full test isolation"
  - "BohriumSetupService uses lazy imports (inside methods) to avoid importing src.services at module level"

patterns-established:
  - "EventHandler Protocol: @runtime_checkable protocol with handle(event: BusEvent) -> None"
  - "Handler injection: EventRouter receives handlers list, handlers receive dependencies via constructor"
  - "Lazy import in BohriumSetupService: defer src.services imports to method call time"

requirements-completed: [MIGR-01, QUAL-04]

# Metrics
duration: 4min
completed: 2026-03-22
---

# Phase 5 Plan 02: EventRouter + Handlers + BohriumSetupService Summary

**EventRouter with single-consumer multi-handler dispatch replacing 130-line event_callback closure, plus WorkspaceHandler and BohriumSetupService wrappers**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-22T08:48:41Z
- **Completed:** 2026-03-22T08:52:42Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments
- EventRouter consumes from MessageBus in background thread, dispatches to all registered handlers with exception isolation
- PersistenceHandler and SSEHandler filter rules directly migrated from _should_persist_event / _should_skip_push in agent_run_service.py
- SSEHandler supports both async (loop present) and sync (worker mode) send_cb with run_coroutine_threadsafe
- WorkspaceHandler debounces, snapshots, and uploads workspace only on ToolResultEvent when files change
- BohriumSetupService wraps 4 agent_run_bohrium functions into class-based two-phase API
- 21 unit tests covering all behaviors

## Task Commits

Each task was committed atomically:

1. **Task 1: Implement EventRouter with PersistenceHandler and SSEHandler** - `e694b7d` (feat)
2. **Task 2: Implement WorkspaceHandler and BohriumSetupService** - `c19fd96` (feat)

_TDD: both tasks followed RED (failing import) -> GREEN (implementation) cycle_

## Files Created/Modified
- `matmaster/integration/__init__.py` - Package init with all exports (EventHandler, EventRouter, PersistenceHandler, SSEHandler, WorkspaceHandler, BohriumSetupService)
- `matmaster/integration/event_router.py` - EventRouter + EventHandler Protocol + PersistenceHandler + SSEHandler
- `matmaster/integration/workspace_handler.py` - WorkspaceHandler with debounce and snapshot comparison
- `matmaster/integration/bohrium_setup.py` - BohriumSetupService wrapping agent_run_bohrium functions
- `tests/matmaster/integration/__init__.py` - Test package init
- `tests/matmaster/integration/test_event_router.py` - 16 tests for EventRouter, PersistenceHandler, SSEHandler
- `tests/matmaster/integration/test_workspace_handler.py` - 5 tests for WorkspaceHandler

## Decisions Made
- PersistenceHandler._should_persist_type() exposed as method for type-level filter testing since log_line and llm_token are not in the BusEvent union (they are legacy event types)
- SSEHandler detects async send_cb via asyncio.iscoroutinefunction at construction time for efficiency
- WorkspaceHandler uses injected snapshot_fn and upload_fn for full test isolation without filesystem or OSS
- BohriumSetupService uses lazy imports (from src.services.agent_run_bohrium import ...) inside methods to avoid importing src.services at module level, keeping matmaster package independent

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

None - all components are fully implemented with real logic.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- EventRouter + handlers ready for Plan 03 (service rewrite) to wire into run_agent_sync()
- BohriumSetupService ready for Plan 03 to replace direct agent_run_bohrium function calls
- All 21 tests passing, providing safety net for integration work

## Self-Check: PASSED

All 7 created files verified present. Both task commits (e694b7d, c19fd96) verified in git log.

---
*Phase: 05-integration-quality*
*Completed: 2026-03-22*
