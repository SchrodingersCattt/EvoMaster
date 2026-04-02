---
phase: 31-tech-debt-cleanup
plan: 01
subsystem: testing
tags: [pydantic, protocol, mock, create_autospec, session, llm_provider, bohrium]

# Dependency graph
requires:
  - phase: 25-session-playground
    provides: "Session Protocol with runtime_checkable, PlaygroundContext.session typed as Session | None"
  - phase: 28-src-consumer
    provides: "BohriumSetupService keyword-only callback injection constructor"
provides:
  - "All Category A+B+C test failures resolved (24 tests)"
  - "Session/LLMProvider mocks using create_autospec or Protocol-conforming classes"
  - "BohriumSetupService test construction updated to keyword-only pattern"
affects: [31-02-PLAN]

# Tech tracking
tech-stack:
  added: []
  patterns: ["create_autospec(Session, instance=True) for Session Protocol mocks", "MockLLMProvider from conftest for LLMProvider Protocol mocks", "**kwargs pattern for intercepting keyword-only constructors in mock side_effect"]

key-files:
  created: []
  modified:
    - tests/matmaster/integration/test_subagent_spawn.py
    - tests/matmaster/devshell/test_runner.py
    - tests/matmaster/devshell/test_integration.py
    - tests/matmaster/integration/test_upstream_scenarios.py
    - tests/matmaster/integration/test_e2e_mat_master.py
    - tests/matmaster/integration/test_bohrium_execution_contract.py

key-decisions:
  - "Used create_autospec(Session, instance=True) for Session mocks -- satisfies Pydantic isinstance check while auto-speccing method signatures"
  - "Used MockLLMProvider from conftest instead of create_autospec(LLMProvider) -- async generator chat_stream needs real implementation, autospec returns coroutine"

patterns-established:
  - "Session mock pattern: create_autospec(Session, instance=True) for any test needing a Session-typed value"
  - "LLMProvider mock pattern: use MockLLMProvider class (not autospec) due to async iterator requirement"

requirements-completed: []

# Metrics
duration: 7min
completed: 2026-04-02
---

# Phase 31 Plan 01: Test Failure Fixes Summary

**Resolved 24 Category A+B+C test failures caused by v2.1 Session/BohriumSetupService/LLMProvider API changes using create_autospec and Protocol-conforming mocks**

## Performance

- **Duration:** 7 min
- **Started:** 2026-04-02T03:51:18Z
- **Completed:** 2026-04-02T03:58:30Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- Fixed 20 Session MagicMock failures across 3 test files by replacing MagicMock() with create_autospec(Session, instance=True)
- Fixed 1 BohriumSetupService positional-arg failure by switching to keyword-only construction with injected mock callables
- Fixed 2 _capture_init signature failures by updating to **kwargs pattern matching the new keyword-only constructor
- Fixed 1 LLMProvider mock failure by using MockLLMProvider from conftest instead of bare MagicMock

## Task Commits

Each task was committed atomically:

1. **Task 1: Fix Session MagicMock failures** - `1f3c1b21` (fix)
2. **Task 2: Fix BohriumSetupService signature + LLMProvider mock failures** - `b24a51da` (fix)

## Files Created/Modified
- `tests/matmaster/integration/test_subagent_spawn.py` - Added create_autospec(Session) in _make_ctx()
- `tests/matmaster/devshell/test_runner.py` - Added create_autospec(Session) in _make_runner()
- `tests/matmaster/devshell/test_integration.py` - Added create_autospec(Session) in _make_runner()
- `tests/matmaster/integration/test_upstream_scenarios.py` - Keyword-only BohriumSetupService construction with 4 mock callables
- `tests/matmaster/integration/test_e2e_mat_master.py` - Both _capture_init functions updated to **kwargs
- `tests/matmaster/integration/test_bohrium_execution_contract.py` - MockLLMProvider + updated skill path assertion

## Decisions Made
- Used create_autospec(Session, instance=True) for Session mocks: satisfies Pydantic isinstance(Session) validation while auto-speccing method signatures
- Used MockLLMProvider from conftest for LLMProvider mocks: create_autospec(LLMProvider) creates AsyncMock for chat_stream which returns a coroutine, not an async iterator -- the real class implementation is needed
- test_context.py required no changes: MagicMock(spec=Session) already satisfied the Protocol, and with_execution() bypasses validation via model_copy

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated stale skill path assertion in test_bohrium_execution_contract.py**
- **Found during:** Task 2 (test_skill_sync_spec_load_exp_config_before_bohrium_setup)
- **Issue:** Test asserted `endswith('playground/mat_master/skills')` but v2.1 migration changed skills_root to `matmaster/skills/lazymcp`
- **Fix:** Updated assertion to `endswith('matmaster/skills/lazymcp')`
- **Files modified:** tests/matmaster/integration/test_bohrium_execution_contract.py
- **Verification:** Test passes with corrected path
- **Committed in:** b24a51da (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix)
**Impact on plan:** Necessary for test correctness after migration. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Category A+B+C test failures are all resolved (24 tests)
- 1294 tests pass in the matmaster test suite
- Remaining failures are Category D (7 tests in stale script files, handled by Plan 02) and Category E (3 tests requiring live LLM API)
- Ready for Plan 02 execution

---
*Phase: 31-tech-debt-cleanup*
*Completed: 2026-04-02*
