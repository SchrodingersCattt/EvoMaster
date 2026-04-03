---
phase: 32-kernel-generator-tool-runtime-v2
plan: 01
subsystem: types
tags: [pydantic, frozen-model, enum, dataclass, tool-runtime-v2]

# Dependency graph
requires:
  - phase: 31-tech-debt-cleanup
    provides: clean baseline with v2.1 complete
provides:
  - Tool Runtime v2 type system (ToolPlane, SessionCapabilities, RuntimeTopology, ToolSpec, ResourceClaim, ToolBinding, ToolInstance, ToolDecision)
  - ToolResult payload + meta fields replacing info
  - SessionCapabilities type in session.py (Phase 34 activation)
affects: [32-02 ToolCatalog, 32-03 Kernel generator, 33 ToolRunner, 34 Exp/Service, 35 constraint migration]

# Tech tracking
tech-stack:
  added: []
  patterns: [frozen Pydantic models for immutable type contracts, frozen dataclass for callable-bearing value objects, frozenset coercion for set-typed fields]

key-files:
  created:
    - matmaster/types/topology.py
    - matmaster/types/tool_spec.py
    - matmaster/types/tool_decision.py
    - tests/matmaster/types/test_topology.py
    - tests/matmaster/types/test_tool_spec.py
    - tests/matmaster/types/test_tool_decision.py
  modified:
    - matmaster/tools/tool_result.py
    - matmaster/types/events.py
    - matmaster/core/hooks.py
    - matmaster/hooks/output_processor.py
    - matmaster/integration/event_payloads.py
    - matmaster/types/session.py
    - tests/matmaster/tools/test_tool_result.py
    - tests/matmaster/types/test_events.py
    - tests/matmaster/core/test_hooks.py
    - tests/matmaster/hooks/test_output_processor.py
    - tests/matmaster/integration/test_event_router.py

key-decisions:
  - "ToolInstance uses frozen dataclass (not Pydantic) because it holds a callable executor"
  - "SessionCapabilities added to session.py as import + comment, not Protocol property, to avoid breaking existing implementations"
  - "event_payloads.py reads from new 'payload' key but outputs as 'info' for SSE frontend contract backward compatibility"

patterns-established:
  - "Tool Runtime v2 frozen models: all new types use ConfigDict(frozen=True)"
  - "ToolInstance frozen dataclass: combines spec + binding + executor callable"
  - "ToolPlane(str, Enum): string-valued enum for plane categorization"

requirements-completed: [TOBJ-01, TOBJ-02, TOBJ-03, TOBJ-04, TOBJ-05, TOBJ-06, TOBJ-07, TOBJ-08, TRES-01]

# Metrics
duration: 10min
completed: 2026-04-02
---

# Phase 32 Plan 01: Tool Runtime v2 Type System + ToolResult Upgrade Summary

**8 frozen types + 1 enum defining Tool Runtime v2 object model, plus ToolResult info->payload+meta upgrade with full consumer chain sync**

## Performance

- **Duration:** 10 min
- **Started:** 2026-04-02T09:24:02Z
- **Completed:** 2026-04-02T09:34:06Z
- **Tasks:** 2
- **Files modified:** 17

## Accomplishments
- Created complete Tool Runtime v2 type system: ToolPlane enum (4 planes), SessionCapabilities, RuntimeTopology, ToolSpec, ResourceClaim, ToolBinding, ToolInstance, ToolDecision
- Upgraded ToolResult from single info field to payload + meta separation, enabling structured metadata without data loss
- Synced all 10 consumer files (5 source + 5 test) including EventEmitterHook, OutputProcessorHook, event_payloads, and integration tests
- 24 new type tests + 1316 existing tests passing (4 pre-existing failures unrelated to changes)

## Task Commits

Each task was committed atomically:

1. **Task 1: Tool Runtime v2 type system (TDD RED)** - `22e41278` (test)
2. **Task 1: Tool Runtime v2 type system (TDD GREEN)** - `82a04ceb` (feat)
3. **Task 2: ToolResult info -> payload + meta upgrade** - `1f2c33ef` (feat)

_Note: Task 1 followed TDD flow with separate RED and GREEN commits_

## Files Created/Modified

### Created
- `matmaster/types/topology.py` - ToolPlane enum, SessionCapabilities, RuntimeTopology frozen models
- `matmaster/types/tool_spec.py` - ToolSpec, ResourceClaim, ToolBinding frozen Pydantic + ToolInstance frozen dataclass
- `matmaster/types/tool_decision.py` - ToolDecision frozen model with allow/deny Literal
- `tests/matmaster/types/test_topology.py` - 8 tests for topology types
- `tests/matmaster/types/test_tool_spec.py` - 9 tests for tool spec types
- `tests/matmaster/types/test_tool_decision.py` - 5 tests for tool decision

### Modified
- `matmaster/tools/tool_result.py` - info field removed, payload + meta added
- `matmaster/types/events.py` - ToolResultEvent.info -> .payload
- `matmaster/core/hooks.py` - EventEmitterHook.post_tool_call: info=result.info -> payload=result.payload
- `matmaster/hooks/output_processor.py` - dict(result.info) -> dict(result.payload), info={} -> payload={}
- `matmaster/integration/event_payloads.py` - payload.get('info') -> payload.get('payload') for SSE contract
- `matmaster/types/session.py` - Added SessionCapabilities import and Phase 34 activation comment
- 5 test files updated with .info -> .payload assertions

## Decisions Made
- **ToolInstance as frozen dataclass**: ToolInstance holds a callable executor which Pydantic cannot serialize/validate. Frozen dataclass preserves immutability without Pydantic overhead.
- **SessionCapabilities as import-only in session.py**: Adding capabilities as a mandatory Protocol property would break existing LocalSession/SSHSession. Deferred to Phase 34.
- **SSE contract backward compatibility**: event_payloads.py reads from the new `'payload'` model_dump key but outputs `'info'` in the SSE payload to maintain frontend contract compatibility.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed event_payloads.py reading stale 'info' key from model_dump**
- **Found during:** Task 2 (ToolResult info -> payload upgrade)
- **Issue:** event_payloads.py L115 reads `payload.get('info')` from ToolResultEvent model_dump, which would silently return empty dict after the field rename to `payload`
- **Fix:** Changed to `payload.get('payload')` while keeping SSE output key as `'info'` for frontend backward compat
- **Files modified:** matmaster/integration/event_payloads.py
- **Verification:** test_event_router.py persistence and SSE handler tests pass
- **Committed in:** 1f2c33ef (Task 2 commit)

**2. [Rule 1 - Bug] Fixed test_event_router.py ToolResultEvent constructor using old 'info=' kwarg**
- **Found during:** Task 2
- **Issue:** Two test instances constructing ToolResultEvent with info= kwarg would fail after field rename
- **Fix:** Changed info= to payload= in test constructors, kept assertion on SSE output as 'info' (correct behavior)
- **Files modified:** tests/matmaster/integration/test_event_router.py
- **Verification:** test_event_router.py passes
- **Committed in:** 1f2c33ef (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (2 Rule 1 bugs)
**Impact on plan:** Both fixes were essential to prevent silent data loss in SSE pipeline. No scope creep.

## Issues Encountered
- 4 pre-existing test failures discovered (web_search rename, bohrium skill path, import audit, real API test) -- all unrelated to this plan's changes

## Known Stubs
None -- all types are fully defined with concrete implementations.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All 8 types + 1 enum available for import from matmaster.types
- ToolResult payload + meta fields ready for Plan 02 ToolCatalog/ToolRunner consumption
- SessionCapabilities type ready for Plan 02 RuntimeTopology integration
- Pre-existing test failures should be addressed in a separate maintenance task

---
## Self-Check: PASSED

All 12 key files verified present. All 3 commit hashes verified in git log.

---
*Phase: 32-kernel-generator-tool-runtime-v2*
*Completed: 2026-04-02*
