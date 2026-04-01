---
phase: 28-src-consumer
plan: 03
subsystem: api
tags: [pydantic, message-types, ssh-session, dependency-inversion, callback-injection]

# Dependency graph
requires:
  - phase: 28-02
    provides: "BohriumSetupService callback injection pattern, bohrium_env module"
  - phase: 25-session-playground
    provides: "matmaster native SSHSession (merged SSHSession+SSHEnv)"
provides:
  - "chat_history.py consuming matmaster native message types"
  - "agent_run_bohrium.py consuming matmaster native SSHSession"
  - "agent_run_service.py with top-level callback imports"
  - "Phase 28 complete: matmaster/ has zero src/ reverse dependencies"
affects: [quality-gate, evomaster-removal]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "matmaster flat ToolCallData format in chat_history serialization"
    - "Direct SSHSession method calls (no _env intermediate)"

key-files:
  created: []
  modified:
    - src/services/chat_history.py
    - src/services/agent_run_bohrium.py
    - src/services/agent_run_service.py

key-decisions:
  - "Reversed _adapt_tool_calls_format direction: now converts legacy evomaster nested to matmaster flat (backward-compatible with historical DB data)"
  - "Simplified events_to_messages to use direct matmaster types (no second format conversion needed)"
  - "Hoisted agent_run_bohrium 4-function import from method body to file-level in agent_run_service.py"

patterns-established:
  - "matmaster flat tool_calls format: {id, name, arguments: dict} as the canonical serialization"
  - "Direct SSHSession duck-typing: pass ssh_session to _upload_directory instead of ssh_session._env"

requirements-completed: [CONS-03, CONS-04, INVR-01]

# Metrics
duration: 6min
completed: 2026-04-01
---

# Phase 28 Plan 03: src Consumer Migration Summary

**chat_history.py switched to matmaster message types (flat ToolCallData), agent_run_bohrium.py switched to matmaster SSHSession (no _env), completing Phase 28 src reverse dependency elimination**

## Performance

- **Duration:** 6 min
- **Started:** 2026-04-01T12:56:18Z
- **Completed:** 2026-04-01T13:02:10Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- chat_history.py fully migrated from evomaster.utils.types to matmaster.types.messages, including reversed _adapt_tool_calls_format, flat _tool_call_from_event, ToolCallData validation, and tool_name field naming
- agent_run_bohrium.py SSHSession import switched to matmaster, _env attribute access eliminated in _sync_skills_to_ssh_session
- agent_run_service.py callback imports hoisted to file-level for cleanliness
- All 47 tests pass (17 events_to_messages + 12 import_audit + 18 bohrium tests)
- Phase 28 requirements CONS-03, CONS-04, INVR-01 fully satisfied

## Task Commits

Each task was committed atomically:

1. **Task 1: chat_history.py message type switch** - `ce82e544` (feat)
2. **Task 2: agent_run_bohrium.py SSHSession + agent_run_service.py import hoist** - `878aac63` (feat)

## Files Created/Modified
- `src/services/chat_history.py` - Switched from evomaster to matmaster message types; reversed _adapt_tool_calls_format; simplified events_to_messages
- `src/services/agent_run_bohrium.py` - Switched SSHSession/SSHSessionConfig import to matmaster; eliminated ssh_session._env access
- `src/services/agent_run_service.py` - Hoisted agent_run_bohrium function imports and functools.partial to file-level

## Decisions Made
- Reversed _adapt_tool_calls_format to convert legacy evomaster nested format to matmaster flat format, maintaining backward compatibility with historical DB data that may still contain nested tool_calls
- Simplified events_to_messages by removing the second format conversion layer (previously converting evomaster nested dicts back to matmaster types; now both paths use matmaster natively)
- Hoisted inline imports to file-level in agent_run_service.py since src -> src is a valid forward dependency direction

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

None.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 28 (src-consumer) is fully complete: all 4 requirements (INVR-01, INVR-02, CONS-03, CONS-04) are satisfied
- matmaster/ has zero reverse dependencies on src/ (verified by import audit: `grep "from src." matmaster/` returns empty)
- Remaining evomaster dependencies in matmaster/ are in bash_tool.py (LocalSession) and other Phase 28+ scope items
- Ready for quality gate verification and milestone progression

## Self-Check: PASSED

- All 3 modified files exist on disk
- Both task commits verified (ce82e544, 878aac63)
- SUMMARY.md created at expected path

---
*Phase: 28-src-consumer*
*Completed: 2026-04-01*
