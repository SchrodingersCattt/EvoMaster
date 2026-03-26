---
phase: 11-subagent-spawn
plan: 02
subsystem: core
tags: [sub-agent, exp-layer, spawn-fn, stop-event, source-override, closure-injection]

# Dependency graph
requires:
  - phase: 11-subagent-spawn
    plan: 01
    provides: SubAgentTool class with spawn_fn closure injection and explore.toml
  - phase: 08-builtintool-tools
    provides: BuiltinTool ABC base class
provides:
  - Exp._make_spawn_fn closure factory connecting Tool layer to Exp layer
  - source_override parameter for child EventEmitterHook source prefix
  - SubAgentTool registration in Exp.build_runtime when configured
  - stop_event injection chain (Exp.run -> SubAgentTool._stop_event -> spawn_fn -> child kernel)
  - Updated SubAgentTool with _stop_event attribute and 3-arg spawn_fn call
affects: [11-03 event routing, service layer integration]

# Tech tracking
tech-stack:
  added: []
  patterns: [spawn_fn closure captures parent ctx/bus for child agent creation, stop_event injection via tool attribute mutation before kernel.run]

key-files:
  created:
    - tests/matmaster/integration/test_subagent_spawn.py
  modified:
    - matmaster/core/exp.py
    - matmaster/tools/builtin/sub_agent_tool.py
    - matmaster/exps/direct.toml
    - tests/matmaster/tools/test_sub_agent_tool.py

key-decisions:
  - "spawn_fn signature: (exp_name, task, stop_event=None) -> str with Callable[..., str] type annotation"
  - "source_override=None default preserves backward compat for all existing callers"
  - "SubAgentTool registered after hooks creation (step 4b) to use spawn_fn with hooks context"
  - "stop_event injected via tool attribute mutation (tool._stop_event = stop_event) in Exp.run()"
  - "Used getattr for tool_registry access in run() to handle MagicMock(spec=AgentRuntimeSpec) in tests"

patterns-established:
  - "stop_event injection chain: Exp.run() -> SubAgentTool._stop_event -> spawn_fn 3rd arg -> child kernel.run(stop_event=)"
  - "source_override for child agent EventEmitterHook: 'MatMaster:{exp_name}' prefix"

requirements-completed: [SUBA-01, SUBA-03, SUBA-05]

# Metrics
duration: 6min
completed: 2026-03-25
---

# Phase 11 Plan 02: Exp Layer Spawn Integration Summary

**Exp._make_spawn_fn closure wiring SubAgentTool to Exp layer with shared context, source prefix, and stop_event propagation chain**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-25T07:43:44Z
- **Completed:** 2026-03-25T07:50:15Z
- **Tasks:** 2 (1 standard + 1 TDD: RED + GREEN)
- **Files modified:** 5

## Accomplishments
- Exp._make_spawn_fn static method creating spawn_fn closure with 3-arg signature (exp_name, task, stop_event)
- source_override parameter on build_runtime for child EventEmitterHook source prefixing
- SubAgentTool registration in build_runtime when "sub_agent" in config.tools.builtin
- Complete stop_event propagation chain: Exp.run() -> SubAgentTool._stop_event -> spawn_fn -> child kernel.run
- 9 integration tests covering spawn lifecycle, cleanup guarantee, context sharing, source prefix, stop_event propagation, recursion guard
- 63 total tests pass across exp, integration, and unit test suites (0 regressions)

## Task Commits

Each task was committed atomically (Task 2 follows TDD flow):

1. **Task 1: Exp layer changes + direct.toml** - `08296cb` (feat)
2. **Task 2 RED: Failing integration tests** - `dfd3f56` (test)
3. **Task 2 GREEN: SubAgentTool _stop_event + implementation** - `b520d72` (feat)

## Files Created/Modified
- `matmaster/core/exp.py` - Added _make_spawn_fn, source_override parameter, SubAgentTool registration in build_runtime, stop_event injection in run()
- `matmaster/tools/builtin/sub_agent_tool.py` - Added _stop_event attribute, 3-arg spawn_fn call in _execute, Callable[..., str] type annotation
- `matmaster/exps/direct.toml` - Added "sub_agent" to tools.builtin list
- `tests/matmaster/integration/test_subagent_spawn.py` - 9 integration tests for spawn lifecycle
- `tests/matmaster/tools/test_sub_agent_tool.py` - Updated assertion to match 3-arg spawn_fn call

## Decisions Made
- spawn_fn uses `Callable[..., str]` instead of explicit 3-arg typing for flexibility with future signature evolution
- source_override defaults to None, preserving backward compatibility for all existing callers (agent_run_service.py, devshell)
- stop_event injected via `tool._stop_event = stop_event` attribute mutation in Exp.run(), keeping build_runtime pure (stop_event is a runtime concern, not assembly)
- Used `getattr(runtime.spec, "tool_registry", None)` instead of direct attribute access for robustness with MagicMock(spec=AgentRuntimeSpec) in existing tests

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed MagicMock attribute access for tool_registry in run()**
- **Found during:** Task 1 (Exp.run() stop_event injection)
- **Issue:** Existing test uses `MagicMock(spec=AgentRuntimeSpec)` which restricts attribute access; direct `runtime.spec.tool_registry` raised AttributeError
- **Fix:** Used `getattr(runtime.spec, "tool_registry", None)` instead of direct attribute access
- **Files modified:** matmaster/core/exp.py
- **Verification:** All 41 existing test_exp.py tests pass
- **Committed in:** 08296cb (Task 1 commit)

**2. [Rule 1 - Bug] Updated Plan 01 test assertion for 3-arg spawn_fn call**
- **Found during:** Task 2 GREEN (SubAgentTool _stop_event upgrade)
- **Issue:** Plan 01 `test_execute_calls_spawn_fn` used `assert_called_once_with("explore", "find files")` but spawn_fn now called with 3 args
- **Fix:** Changed assertion to `assert_called_once_with("explore", "find files", None)` to match new 3-arg signature
- **Files modified:** tests/matmaster/tools/test_sub_agent_tool.py
- **Verification:** All 13 Plan 01 unit tests pass
- **Committed in:** b520d72 (Task 2 GREEN commit)

---

**Total deviations:** 2 auto-fixed (2 bug fixes)
**Impact on plan:** Both auto-fixes necessary for test compatibility. No scope creep.

## Issues Encountered
- Worktree was on test branch, required rebase onto refactor/matmaster-playground-exp-agent-v2 to get Plan 01 artifacts
- uv sync --extra dev needed to install pytest in worktree venv

## User Setup Required

None - no external service configuration required.

## Known Stubs

None - all functionality is fully wired.

## Next Phase Readiness
- spawn_fn closure fully operational: creates child Exp, runs kernel, returns result, cleans up
- stop_event propagation chain complete: parent cancel signal flows to child agent
- Source prefix "MatMaster:{exp_name}" enables event routing differentiation in Plan 03
- direct.toml includes sub_agent, explore.toml excludes it (recursion guard verified)

## Self-Check: PASSED

All artifacts verified:
- matmaster/core/exp.py: FOUND
- matmaster/tools/builtin/sub_agent_tool.py: FOUND
- matmaster/exps/direct.toml: FOUND
- tests/matmaster/integration/test_subagent_spawn.py: FOUND (9 tests)
- tests/matmaster/tools/test_sub_agent_tool.py: FOUND
- Commit 08296cb (feat - Task 1): FOUND
- Commit dfd3f56 (test - Task 2 RED): FOUND
- Commit b520d72 (feat - Task 2 GREEN): FOUND

---
*Phase: 11-subagent-spawn*
*Completed: 2026-03-25*
