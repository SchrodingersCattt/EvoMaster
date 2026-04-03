---
phase: 33-toolrunner-toolscheduler
plan: 02
subsystem: core
tags: [asyncio, rwlock, semaphore, scheduler, concurrency]

requires:
  - phase: 32-kernel-generator-tool-runtime
    provides: ResourceClaim type in matmaster/types/tool_spec.py

provides:
  - ToolScheduler with exclusive/shared_read/counted resource scheduling
  - _RWLock readers-writer lock using pure asyncio primitives
  - SchedulerTicket acquire receipt for targeted release

affects: [33-03, 34-exp-integration]

tech-stack:
  added: []
  patterns: [per-resource lazy lock creation, deadline-based timeout in asyncio Condition, rollback on partial acquire]

key-files:
  created:
    - matmaster/core/tool_scheduler.py
    - tests/matmaster/core/test_tool_scheduler.py
  modified: []

key-decisions:
  - "_RWLock uses asyncio.Lock + Condition + int counters -- no third-party lock library"
  - "SchedulerTicket is a plain dataclass, not frozen, to allow construction during acquire"
  - "counted limit=None defensively defaults to 1 with warning log"

patterns-established:
  - "Deadline-based timeout: compute deadline once, recalculate remaining each wait iteration"
  - "Rollback on partial failure: acquire sequentially, release already-acquired on any timeout"

requirements-completed: [TRUN-04]

duration: 3min
completed: 2026-04-02
---

# Phase 33 Plan 02: ToolScheduler Summary

**ResourceClaim-based tool scheduling with _RWLock (exclusive/shared_read) and asyncio.Semaphore (counted), pure asyncio primitives, 199 LOC**

## Performance

- **Duration:** 3 min
- **Started:** 2026-04-02T11:39:52Z
- **Completed:** 2026-04-02T11:43:42Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 2

## Accomplishments

- _RWLock internal class: classic readers-writer lock with asyncio.Lock + Condition + int counters
- ToolScheduler: per-resource scheduling for exclusive (write lock), shared_read (read lock), and counted (semaphore) modes
- SchedulerTicket: acquire receipt enabling targeted reverse-order release
- Automatic rollback of partially-acquired resources on timeout
- Defensive handling of counted mode with limit=None (defaults to 1)

## Task Commits

Each task was committed atomically (TDD flow):

1. **Task 1 RED: Failing tests** - `b18505cb` (test)
2. **Task 1 GREEN: Implementation** - `6ea230e0` (feat)

## Files Created/Modified

- `matmaster/core/tool_scheduler.py` - ToolScheduler, _RWLock, SchedulerTicket (199 lines)
- `tests/matmaster/core/test_tool_scheduler.py` - 10 tests across 6 classes (198 lines)

## Decisions Made

- _RWLock uses asyncio.Lock + Condition + int counters -- no third-party lock library (per D-02 constraint)
- SchedulerTicket is a plain dataclass (not frozen) to allow list construction during acquire
- counted mode with limit=None defensively defaults to 1 with warning log, no exception

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Known Stubs

None - all functionality is fully implemented and tested.

## Next Phase Readiness

- ToolScheduler ready for integration into FullToolRunner (Plan 33-03)
- acquire/release API matches the ToolRunner execution chain design
- All concurrency/timeout/multi-resource scenarios covered by tests

## Self-Check: PASSED

- All created files exist on disk
- All commit hashes found in git log
- 10/10 tests passing

---
*Phase: 33-toolrunner-toolscheduler*
*Completed: 2026-04-02*
