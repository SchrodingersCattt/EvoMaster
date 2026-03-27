---
phase: 15-hook
plan: 01
subsystem: core
tags: [async, hooks, protocol, asyncio, bridge, pytest-asyncio]

# Dependency graph
requires:
  - phase: 11-subagent-spawn
    provides: Hook Protocol and BaseHook definitions, EventEmitterHook, run_* helpers
provides:
  - async run_* hook helper functions (7 functions)
  - async Hook Protocol + BaseHook defaults
  - async EventEmitterHook (5 methods)
  - async OutputProcessorHook / AssistantStateHook / SkillHitHook
  - async DevStreamHook (4 overridden methods)
  - _sync_call_async bridge in AgentKernel for 13 call sites
  - pytest-asyncio dev dependency + asyncio_mode=auto config
affects: [15-hook-02, phase-16-messagebus, phase-17-kernel-async]

# Tech tracking
tech-stack:
  added: [pytest-asyncio]
  patterns: [async Hook Protocol, _sync_call_async bridge pattern, asyncio_mode=auto]

key-files:
  created: []
  modified:
    - matmaster/core/hooks.py
    - matmaster/core/agent.py
    - matmaster/hooks/output_processor.py
    - matmaster/hooks/assistant_state.py
    - matmaster/hooks/skill_hit.py
    - matmaster/devshell/stream_hook.py
    - pyproject.toml
    - tests/matmaster/core/test_hooks.py
    - tests/matmaster/core/test_agent.py
    - tests/matmaster/core/test_exp.py
    - tests/matmaster/hooks/test_output_processor.py
    - tests/matmaster/hooks/test_assistant_state.py
    - tests/matmaster/hooks/test_skill_hit.py
    - tests/matmaster/devshell/test_stream_hook.py
    - tests/matmaster/integration/test_subagent_event_routing.py

key-decisions:
  - "Hook Protocol + BaseHook async: all 7 methods now async def (prerequisite for run_* async)"
  - "Removed getattr backward compat from run_on_segment_complete/run_guard_blocked (BaseHook provides all methods)"
  - "bus.emit stays sync in all Hook implementations per D-08 (Phase 16 scope)"
  - "Added _bridge_loop + _sync_call_async to agent.py for sync Kernel -> async hook bridging"
  - "Added pytest-asyncio + asyncio_mode=auto for async test support"

patterns-established:
  - "_sync_call_async(coro, loop) bridge: sync Kernel wraps async run_* calls via dedicated event loop"
  - "asyncio_mode=auto: all async def test functions automatically run as coroutines"

requirements-completed: [HOOK-01, HOOK-03]

# Metrics
duration: 19min
completed: 2026-03-27
---

# Phase 15 Plan 01: Hook Async Summary

**Async run_* helpers with await + 5 Hook implementations async + Kernel _sync_call_async bridge for 13 call sites + full test migration to pytest-asyncio**

## Performance

- **Duration:** 19 min
- **Started:** 2026-03-27T14:44:24Z
- **Completed:** 2026-03-27T15:03:34Z
- **Tasks:** 2
- **Files modified:** 16

## Accomplishments
- All 7 run_* hook helper functions converted to async def with await on each hook method
- Hook Protocol and BaseHook: all 7 methods made async def (prerequisite for run_* async)
- EventEmitterHook (5 methods), OutputProcessorHook, AssistantStateHook, SkillHitHook, DevStreamHook all async
- AgentKernel: added _bridge_loop + _sync_call_async, all 13 run_* call sites bridged
- 6 test Hook classes in test_agent.py converted to async def
- All hook-related tests migrated to async via pytest-asyncio asyncio_mode=auto
- 151 tests pass across all affected test files

## Task Commits

Each task was committed atomically:

1. **Task 1: run_* helpers async + 5 Hook implementations async + DevStreamHook async** - `1dcd95d` (feat)
2. **Task 2: Kernel bridge 13 run_* calls + test_agent.py 6 sync Hook classes async + test migration** - `bc82cf5` (feat)

## Files Created/Modified
- `matmaster/core/hooks.py` - Hook Protocol/BaseHook async + 7 run_* helpers async + EventEmitterHook async
- `matmaster/core/agent.py` - _bridge_loop + _sync_call_async + 13 bridged run_* calls
- `matmaster/hooks/output_processor.py` - async def post_tool_call
- `matmaster/hooks/assistant_state.py` - async def pre_llm_call
- `matmaster/hooks/skill_hit.py` - async def post_tool_call
- `matmaster/devshell/stream_hook.py` - 4 overridden methods async def
- `pyproject.toml` - pytest-asyncio dev dep + asyncio_mode=auto
- `tests/matmaster/core/test_hooks.py` - full async migration (all helpers + hook classes + tests)
- `tests/matmaster/core/test_agent.py` - 6 Hook classes async (SkipHook/StopHook/RecordingHook/ChunkRecordingHook/SegmentRecordingHook/GuardBlockRecorder)
- `tests/matmaster/core/test_exp.py` - 2 spawn_id tests async with await
- `tests/matmaster/hooks/test_output_processor.py` - all tests async with await
- `tests/matmaster/hooks/test_assistant_state.py` - all tests async with await
- `tests/matmaster/hooks/test_skill_hit.py` - all tests async with await
- `tests/matmaster/devshell/test_stream_hook.py` - all tests async with await
- `tests/matmaster/integration/test_subagent_event_routing.py` - 1 test async with await

## Decisions Made
- Hook Protocol + BaseHook made async as prerequisite (was sync on this branch; refactor branch had them async from Phase 12)
- Removed getattr backward-compat from run_on_segment_complete and run_guard_blocked since BaseHook now provides all 7 methods
- bus.emit calls kept sync in all Hook implementations (per D-08: queue.Queue.put is microsecond-level, Phase 16 scope)
- Added pytest-asyncio as dev dependency and configured asyncio_mode=auto in pyproject.toml

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Hook Protocol + BaseHook async conversion (prerequisite)**
- **Found during:** Task 1 (run_* helpers async)
- **Issue:** Plan assumed Protocol/BaseHook were already async from Phase 12, but this worktree is based on test branch where they were still sync. run_* helpers cannot await sync methods.
- **Fix:** Added async def to all 7 Protocol methods and all 7 BaseHook default methods
- **Files modified:** matmaster/core/hooks.py
- **Verification:** inspect.iscoroutinefunction confirms all methods async
- **Committed in:** 1dcd95d (Task 1 commit)

**2. [Rule 3 - Blocking] pytest-asyncio dependency + configuration**
- **Found during:** Task 2 (test migration)
- **Issue:** pytest-asyncio not installed, async test functions would not run correctly
- **Fix:** Added pytest-asyncio>=0.24.0 to dev dependencies, configured asyncio_mode=auto in pyproject.toml
- **Files modified:** pyproject.toml, uv.lock
- **Verification:** All async tests run and pass
- **Committed in:** bc82cf5 (Task 2 commit)

**3. [Rule 3 - Blocking] Additional test files needed async migration**
- **Found during:** Task 2 (test migration)
- **Issue:** Plan listed 6 test files but 3 additional files (test_stream_hook.py, test_exp.py, test_subagent_event_routing.py) also call async Hook methods directly
- **Fix:** Migrated affected test functions to async def with await
- **Files modified:** tests/matmaster/devshell/test_stream_hook.py, tests/matmaster/core/test_exp.py, tests/matmaster/integration/test_subagent_event_routing.py
- **Verification:** All 151 hook-related tests pass
- **Committed in:** bc82cf5 (Task 2 commit)

---

**Total deviations:** 3 auto-fixed (3 blocking)
**Impact on plan:** All auto-fixes necessary for correctness. No scope creep -- prerequisite work and test discovery within plan's intent.

## Issues Encountered
None -- all issues resolved as deviations above.

## Known Stubs
None -- no stubs or placeholders introduced.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All Hook implementations (except ConfirmationHook) are async
- Kernel bridges all async hook calls via _sync_call_async
- Ready for Plan 02 (ConfirmationHook async + remaining integration)
- _bridge_loop pattern will be removed in Phase 17 when Kernel itself becomes async

---
*Phase: 15-hook*
*Completed: 2026-03-27*
