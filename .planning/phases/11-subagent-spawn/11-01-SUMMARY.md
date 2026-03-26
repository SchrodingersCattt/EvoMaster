---
phase: 11-subagent-spawn
plan: 01
subsystem: tools
tags: [sub-agent, builtin-tool, toml-config, spawn-fn, recursion-guard]

# Dependency graph
requires:
  - phase: 08-builtintool-tools
    provides: BuiltinTool ABC base class with ClassVar Protocol
  - phase: 10-tool-description-system-prompt
    provides: Claude Code quality description standard and developer_instructions pattern
provides:
  - SubAgentTool class with spawn_fn closure injection and recursion guard
  - explore.toml sub-agent exp config with read-only tools and PRMT-03 prompt
  - SubAgentTool exported from matmaster.tools.builtin package
affects: [11-02 Exp integration, 11-03 event routing]

# Tech tracking
tech-stack:
  added: []
  patterns: [spawn_fn closure injection for cross-layer decoupling, dual-layer recursion guard (schema + runtime)]

key-files:
  created:
    - matmaster/tools/builtin/sub_agent_tool.py
    - matmaster/exps/explore.toml
    - tests/matmaster/tools/test_sub_agent_tool.py
  modified:
    - matmaster/tools/builtin/__init__.py

key-decisions:
  - "spawn_fn typed as Callable[[str, str], str] | None for Plan 02 forward compat"
  - "Test stubs use Mock(return_value=...) not lambda to survive Plan 02 3-arg signature change"
  - "explore.toml max_turns=50 (lower than parent 200) to bound sub-agent execution"
  - "explore.toml mcp='' and skills.enabled=false for minimal sub-agent scope"

patterns-established:
  - "spawn_fn closure injection: tool does not depend on Exp, receives callable at construction"
  - "Dual recursion guard: schema-layer (TOML omits sub_agent) + runtime-layer (spawn_fn=None check)"

requirements-completed: [SUBA-01, SUBA-02, SUBA-04, PRMT-03]

# Metrics
duration: 3min
completed: 2026-03-25
---

# Phase 11 Plan 01: SubAgentTool + explore.toml Summary

**SubAgentTool with spawn_fn closure injection, dual-layer recursion guard, and explore.toml read-only sub-agent exp definition**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-25T07:33:55Z
- **Completed:** 2026-03-25T07:37:02Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 4

## Accomplishments
- SubAgentTool class inheriting BuiltinTool with spawn_fn closure injection pattern
- Dual-layer recursion protection: explore.toml omits sub_agent from tools.builtin (schema-layer), spawn_fn=None returns error string (runtime-layer)
- explore.toml with PRMT-03 exploration sub-task prompt, read-only tool set, max_turns=50
- 13 unit tests covering spawn, recursion guard, input validation, class vars, and TOML loading
- 837 total tests pass (0 regressions)

## Task Commits

Each task was committed atomically (TDD flow):

1. **Task 1 RED: Failing tests** - `33aa709` (test)
2. **Task 1 GREEN: Implementation** - `2de8be3` (feat)

## Files Created/Modified
- `matmaster/tools/builtin/sub_agent_tool.py` - SubAgentTool class with spawn_fn injection, recursion guard, input validation
- `matmaster/exps/explore.toml` - Exploration sub-agent exp config: read-only tools, PRMT-03 prompt, max_turns=50
- `matmaster/tools/builtin/__init__.py` - Added SubAgentTool import and __all__ export
- `tests/matmaster/tools/test_sub_agent_tool.py` - 13 unit tests (9 SubAgentTool + 4 explore.toml)

## Decisions Made
- spawn_fn typed as `Callable[[str, str], str] | None` -- Plan 02 will evolve to 3-arg signature, tests use Mock for forward compat
- explore.toml max_turns=50 to bound sub-agent execution relative to parent's 200
- explore.toml uses mcp="" and skills.enabled=false for minimal sub-agent scope
- SubAgentTool description follows Phase 10 Claude Code quality standard (overview + usage bullets)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- Worktree was on wrong branch (test instead of refactor), required rebase onto refactor/matmaster-playground-exp-agent-v2
- uv sync needed --extra dev to install pytest in worktree venv

## User Setup Required

None - no external service configuration required.

## Known Stubs

None - all functionality is fully wired.

## Next Phase Readiness
- SubAgentTool ready for Plan 02 to integrate spawn_fn creation in Exp.build_runtime()
- explore.toml ready for Plan 02 to load via load_exp_config("explore") in spawn_fn closure
- Plan 02 will evolve spawn_fn signature to 3-arg (exp_name, task, stop_event)

## Self-Check: PASSED

All artifacts verified:
- matmaster/tools/builtin/sub_agent_tool.py: FOUND
- matmaster/exps/explore.toml: FOUND
- tests/matmaster/tools/test_sub_agent_tool.py: FOUND
- Commit 33aa709 (test): FOUND
- Commit 2de8be3 (feat): FOUND

---
*Phase: 11-subagent-spawn*
*Completed: 2026-03-25*
