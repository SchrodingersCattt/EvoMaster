---
phase: 11-subagent-spawn
plan: 03
subsystem: integration
tags: [sub-agent, event-routing, source-prefix, chat-history, normalize, stop-event]

# Dependency graph
requires:
  - phase: 11-subagent-spawn
    plan: 01
    provides: SubAgentTool class with spawn_fn and _stop_event attribute
  - phase: 11-subagent-spawn
    plan: 02
    provides: Exp._make_spawn_fn with source_override, stop_event injection in Exp.run()
provides:
  - normalize_event_source preserves MatMaster:subtype prefix for sub-agent events
  - _normalize_public_source preserves MatMaster:subtype prefix for sub-agent events
  - _is_matmaster_source helper for consistent MatMaster/MatMaster:* source matching
  - chat_history.py compatible with sub-agent prefixed sources at all 4 judgment locations
  - agent_run_service.py injects stop_event into SubAgentTool for service-layer cancel propagation
affects: [frontend SSE source field interpretation, future sub-agent types]

# Tech tracking
tech-stack:
  added: []
  patterns: [_is_matmaster_source helper centralizes MatMaster prefix matching, startswith guard in normalize functions]

key-files:
  created:
    - tests/matmaster/integration/test_subagent_event_routing.py
  modified:
    - src/utils/chat_event_source.py
    - matmaster/integration/event_payloads.py
    - src/services/chat_history.py
    - src/services/agent_run_service.py

key-decisions:
  - "_is_matmaster_source helper added at module level in chat_history.py for DRY source matching"
  - "startswith('MatMaster:') guard placed before fallback return in both normalize functions"
  - "agent_run_service.py stop_event injection uses lazy import of SubAgentTool inside if block"

patterns-established:
  - "MatMaster:subtype prefix convention: sub-agent sources use 'MatMaster:{exp_name}' format throughout pipeline"
  - "_is_matmaster_source() for any code needing to match both plain and prefixed MatMaster sources"

requirements-completed: [SUBA-05, SUBA-06]

# Metrics
duration: 6min
completed: 2026-03-25
---

# Phase 11 Plan 03: Event Routing + Source Prefix Summary

**Extended event pipeline to preserve MatMaster:subtype prefix for sub-agent events, with chat_history source compatibility and service-layer stop_event injection**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-25T07:56:27Z
- **Completed:** 2026-03-25T08:03:01Z
- **Tasks:** 2 (1 standard + 1 TDD: RED + GREEN)
- **Files modified:** 5

## Accomplishments
- normalize_event_source and _normalize_public_source both preserve MatMaster:subtype prefix instead of collapsing to plain MatMaster
- _is_matmaster_source helper replaces all 4 hardcoded source == 'MatMaster' checks in chat_history.py
- agent_run_service.py injects stop_event into SubAgentTool before kernel.run for service-layer cancel propagation (SUBA-05)
- 10 event routing tests covering normalize functions, helper, and EventEmitterHook -> MessageBus pipeline
- 863 total tests pass (0 regressions)

## Task Commits

Each task was committed atomically (Task 2 follows TDD flow):

1. **Task 1: Extend normalize functions + agent_run_service stop_event injection** - `1feedb0` (feat)
2. **Task 2 RED: Failing event routing tests** - `6274821` (test)
3. **Task 2 GREEN: _is_matmaster_source + chat_history replacement** - `0f7b686` (feat)

## Files Created/Modified
- `src/utils/chat_event_source.py` - Added startswith('MatMaster:') prefix preservation in normalize_event_source
- `matmaster/integration/event_payloads.py` - Added startswith("MatMaster:") prefix preservation in _normalize_public_source
- `src/services/agent_run_service.py` - Added SubAgentTool stop_event injection block after spec creation
- `src/services/chat_history.py` - Added _is_matmaster_source helper, replaced all 4 source == 'MatMaster' checks
- `tests/matmaster/integration/test_subagent_event_routing.py` - 10 tests for normalize functions, helper, and EventEmitterHook

## Decisions Made
- _is_matmaster_source helper placed at module level (after imports, before class) for simple DRY pattern
- startswith('MatMaster:') guard placed BEFORE the fallback return in normalize functions to preserve prefix
- agent_run_service.py uses lazy import of SubAgentTool inside the if block to avoid import overhead when SubAgentTool is not used

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Worktree was on test branch, required rebase onto refactor/matmaster-playground-exp-agent-v2 to get Plan 01/02 artifacts
- uv sync --extra dev needed to install pytest in worktree venv

## User Setup Required

None - no external service configuration required.

## Known Stubs

None - all functionality is fully wired.

## Next Phase Readiness
- Sub-agent event pipeline complete: MatMaster:explore events flow through normalize -> chat_history -> frontend without being collapsed
- Service-layer stop_event injection complements Exp.run() injection (both paths covered)
- Phase 11 sub-agent spawn mechanism fully operational across all 3 plans

## Self-Check: PASSED

All artifacts verified:
- src/utils/chat_event_source.py: FOUND (contains startswith guard)
- matmaster/integration/event_payloads.py: FOUND (contains startswith guard)
- src/services/chat_history.py: FOUND (contains _is_matmaster_source)
- src/services/agent_run_service.py: FOUND (contains SubAgentTool injection)
- tests/matmaster/integration/test_subagent_event_routing.py: FOUND (10 tests, 100 lines)
- Commit 1feedb0 (feat - Task 1): FOUND
- Commit 6274821 (test - Task 2 RED): FOUND
- Commit 0f7b686 (feat - Task 2 GREEN): FOUND

---
*Phase: 11-subagent-spawn*
*Completed: 2026-03-25*
