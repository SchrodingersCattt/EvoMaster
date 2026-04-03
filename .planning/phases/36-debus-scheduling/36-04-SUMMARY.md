---
phase: 36-debus-scheduling
plan: 04
subsystem: devshell, scheduling, transport-audit
tags: [SimpleQueue, DevEventObserver, DevEventHook, ToolScheduler, ToolCompiler, bus-deletion]

# Dependency graph
requires:
  - phase: 36-03
    provides: bus-free Exp/ContextCompactor APIs, rewritten integration tests
provides:
  - SimpleQueue-backed DevEventObserver + DevEventHook for DevShell local event collection
  - bus.py physical deletion (zero remaining consumers)
  - Repo-wide DBUS-01 audit: zero MessageBus/EventRouter Python imports in matmaster/src/tests
  - Stateless scheduling boundary lock tests (ToolScheduler generic, ToolCompiler relaxation bounded)
affects: [v2.3-devshell-run-stream, ASCH-01-persistent-shell]

# Tech tracking
tech-stack:
  added: [queue.SimpleQueue for DevShell thread-safe event handoff]
  patterns: [DevEventObserver+DevEventHook local observer, stateless boundary lock regression tests]

key-files:
  created:
    - matmaster/devshell/event_observer.py
  modified:
    - matmaster/devshell/runner.py
    - matmaster/devshell/repl.py
    - matmaster/devshell/cli.py
    - matmaster/devshell/debug_run.py
    - matmaster/types/events.py
    - tests/matmaster/devshell/test_integration.py
    - tests/matmaster/devshell/test_compaction_via_devshell.py
    - tests/matmaster/core/test_tool_scheduler.py
    - tests/matmaster/tools/test_tool_compiler.py
    - tests/matmaster/integration/test_upstream_scenarios.py
    - tests/matmaster/integration/test_compaction_real_api.py
  deleted:
    - matmaster/core/bus.py

key-decisions:
  - "DevEventObserver uses queue.SimpleQueue (not asyncio.Queue) for thread-safe cross-thread event handoff"
  - "DevEventHook converts on_segment_complete to ThoughtEvent/ResponseEvent; pre_tool_call/post_tool_call unused in kernel.run()+FullToolRunner path (D-01)"
  - "bus.py physically deleted after confirming zero import consumers remain"
  - "Stateless scheduling boundary locked via AST import audit + source inspection + API signature tests"

patterns-established:
  - "DevEventObserver pattern: SimpleQueue + DevEventHook(BaseHook) for DevShell-local observability without bus"
  - "Boundary lock pattern: AST-based import audit + source text scan + API signature inspection to prevent silent feature drift"

requirements-completed: [DBUS-01]

# Metrics
duration: 12min
completed: 2026-04-03
---

# Phase 36 Plan 04: DevShell Observer + Scheduling Boundary Lock Summary

**SimpleQueue-backed DevEventObserver replaces DevShell MessageBus, bus.py physically deleted, repo-wide transport audit confirms zero remaining MessageBus/EventRouter Python imports, stateless scheduling boundary locked with explicit regression tests**

## Performance

- **Duration:** 12 min
- **Started:** 2026-04-03T09:26:32Z
- **Completed:** 2026-04-03T09:38:32Z
- **Tasks:** 2
- **Files modified:** 14 (1 created, 12 modified, 1 deleted)

## Accomplishments
- DevShell (runner/repl/cli/debug_run) fully decoupled from MessageBus using SimpleQueue-backed DevEventObserver
- bus.py physically deleted -- zero remaining import consumers across matmaster/, src/, tests/
- Stateless scheduling boundary locked: ToolScheduler proven capability-agnostic, ToolCompiler relaxation bounded to glob/grep/list_dir under local+stateless only
- Phase 36 exit gate passed: `rg -n "MessageBus|EventRouter" matmaster src tests --glob '*.py'` returns only docstring/comment references and one negative assertion test

## Task Commits

Each task was committed atomically:

1. **Task 1: Replace DevShell MessageBus with local observer adapter** - `977f1041` (feat)
2. **Task 2: Lock stateless scheduling boundary + repo-wide audit** - `637d9191` (feat)

## Files Created/Modified
- `matmaster/devshell/event_observer.py` - NEW: DevEventObserver (SimpleQueue) + DevEventHook (BaseHook) for local event collection
- `matmaster/devshell/runner.py` - bus= -> event_observer= parameter, observer hook + compactor sink wiring
- `matmaster/devshell/repl.py` - MessageBus -> DevEventObserver + queue.Empty polling
- `matmaster/devshell/cli.py` - MessageBus -> DevEventObserver in _run_with_event_log
- `matmaster/devshell/debug_run.py` - MessageBus -> DevEventObserver
- `matmaster/core/bus.py` - DELETED (zero remaining consumers)
- `matmaster/types/events.py` - Docstring cleaned: "MessageBus transport" -> "event union type"
- `tests/matmaster/devshell/test_integration.py` - Rewritten for DevEventObserver, fixed pre-existing tool name bug
- `tests/matmaster/devshell/test_compaction_via_devshell.py` - Docstring MessageBus -> event_sink
- `tests/matmaster/core/test_tool_scheduler.py` - Added TestStatelessSchedulingBoundary (3 tests)
- `tests/matmaster/tools/test_tool_compiler.py` - Added TestStatelessCompilerRelaxationBoundary (5 tests)
- `tests/matmaster/integration/test_upstream_scenarios.py` - Removed stale MessageBus import
- `tests/matmaster/integration/test_compaction_real_api.py` - Docstring MessageBus -> event_sink

## Decisions Made
- Used queue.SimpleQueue (not asyncio.Queue) for DevShell -- official Python docs confirm asyncio.Queue is not thread-safe, and DevShell uses worker threads
- DevEventHook pre_tool_call/post_tool_call callbacks exist in the hook but are NOT invoked in the kernel.run()+FullToolRunner path (FullToolRunner D-01 does not dispatch hooks). Tool call events are only available via the generator path (run_stream). This is documented and test expectations updated accordingly.
- bus.py deletion is safe because Plan 03 already removed the last Exp/ContextCompactor bus= parameters, and this plan removed the last DevShell consumers
- Boundary lock tests use AST-based source inspection rather than runtime assertions, providing stronger guarantees against silent feature drift

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed pre-existing test_full_run_with_tool_call failure**
- **Found during:** Task 1 (test rewrite)
- **Issue:** ToolCallingProvider mock used tool name `bash` but ToolCatalog registers `execute_bash`; assertion checked for `tool_call: bash` in terminal output but FullToolRunner D-01 means hooks don't fire for tool calls
- **Fix:** Updated mock to use `execute_bash`, changed assertion to verify final response content and run_result log entry instead of hook-dependent tool_call text
- **Files modified:** tests/matmaster/devshell/test_integration.py
- **Verification:** Test passes with correct assertions matching current FullToolRunner architecture
- **Committed in:** 977f1041 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Pre-existing test bug, fix aligns test with current architecture. No scope creep.

## Issues Encountered
None beyond the pre-existing test fix documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 36 complete: all 4 plans executed, DBUS-01/DBUS-02/DBUS-03 requirements met
- matmaster/ is fully bus-free; the event transport is now direct fanout (service layer) + DevEventObserver (devshell)
- ASCH-01 (persistent-shell scheduling) explicitly deferred with boundary lock tests preventing silent drift
- FUTR-03 (DevShell migration to run_stream()) remains deferred to v2.3+

---
*Phase: 36-debus-scheduling*
*Completed: 2026-04-03*
