---
phase: 28-src-consumer
plan: 02
subsystem: integration
tags: [bohrium, dependency-inversion, callback-injection, import-audit]

# Dependency graph
requires:
  - phase: 28-01
    provides: "matmaster/integration/bohrium_env.py with BOHRIUM_OPENAPI_HOST, BohriumSetupResult, get_bohrium_credentials, get_bohrium_storage_config, inject_bohrium_executor, build_bohrium_skill_remote_env"
provides:
  - "bohrium_setup.py callback-injected BohriumSetupService (no src reverse dependency)"
  - "script_env.py using matmaster-native BOHRIUM_OPENAPI_HOST"
  - "path_adaptor.py and job_service.py using matmaster.integration.bohrium_env instead of evomaster.env.bohrium"
  - "Import audit gates for src and evomaster.env.bohrium fully enforced (xfail removed)"
affects: [28-03, consumer-migration]

# Tech tracking
tech-stack:
  added: []
  patterns: [callback-injection-for-dependency-inversion, functools-partial-for-service-binding]

key-files:
  created:
    - tests/matmaster/test_bohrium_setup_injection.py
  modified:
    - matmaster/integration/bohrium_setup.py
    - matmaster/tools/script_env.py
    - matmaster/adaptors/calculation/path_adaptor.py
    - matmaster/adaptors/calculation/job_service.py
    - tests/matmaster/test_import_audit.py
    - src/services/agent_run_service.py

key-decisions:
  - "BohriumSetupResult imported directly (not under TYPE_CHECKING) since it is a lightweight NamedTuple"
  - "Consumer (agent_run_service.py) uses functools.partial to bind sessions_service into load_credentials_fn and cleanup_fn closures"
  - "Docstring reference to src.services removed to keep grep-based audits clean"

patterns-established:
  - "Callback injection: service classes accept callables instead of importing upstream modules"
  - "functools.partial for binding service dependencies into callback signatures"

requirements-completed: [INVR-01, INVR-02]

# Metrics
duration: 6min
completed: 2026-04-01
---

# Phase 28 Plan 02: src/evomaster Reverse Dependency Elimination Summary

**Callback-injected BohriumSetupService + 3 file import migration eliminating all matmaster -> src/evomaster.env.bohrium reverse dependencies**

## Performance

- **Duration:** 6 min
- **Started:** 2026-04-01T12:46:33Z
- **Completed:** 2026-04-01T12:52:49Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments
- BohriumSetupService refactored from sessions_service injection to 4-callable callback injection, eliminating all 4 lazy imports from src.services.agent_run_bohrium
- script_env.py BOHRIUM_OPENAPI_HOST import switched from src.utils.constant to matmaster.integration.bohrium_env
- path_adaptor.py 2 evomaster.env.bohrium imports and job_service.py 1 evomaster.env.bohrium import switched to matmaster.integration.bohrium_env
- Import audit xfail markers removed for TestNoSrcImportsInMatmaster and TestNoEvomasterEnvBohriumImportsAnywhere; both now enforce as strict gates
- Consumer side (agent_run_service.py) updated to construct BohriumSetupService with functools.partial-bound callables

## Task Commits

Each task was committed atomically:

1. **Task 1: bohrium_setup.py callback injection + import migration** (TDD)
   - `d25f72d1` (test: add failing tests for bohrium_setup callback injection)
   - `f453f9b6` (feat: callback injection for BohriumSetupService + import migration)
2. **Task 2: Remove import audit xfail markers** - `04e5a46e` (feat)

## Files Created/Modified
- `matmaster/integration/bohrium_setup.py` - Refactored to callback injection; 4 callables replace sessions_service
- `matmaster/tools/script_env.py` - BOHRIUM_OPENAPI_HOST from matmaster.integration.bohrium_env
- `matmaster/adaptors/calculation/path_adaptor.py` - 2 imports switched from evomaster.env.bohrium
- `matmaster/adaptors/calculation/job_service.py` - 1 import switched from evomaster.env.bohrium
- `tests/matmaster/test_bohrium_setup_injection.py` - 6 unit tests for callback injection pattern
- `tests/matmaster/test_import_audit.py` - xfail markers removed for src and evomaster.env.bohrium
- `src/services/agent_run_service.py` - Updated consumer to pass callables via functools.partial

## Decisions Made
- BohriumSetupResult imported directly (not under TYPE_CHECKING) since it is a lightweight NamedTuple with no heavy dependencies
- Consumer uses functools.partial to bind sessions_service into load_credentials_fn and cleanup_fn, keeping the callback signatures clean
- Docstring in BohriumSetupService updated to avoid referencing "from src.services..." to keep grep-based audits clean

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Updated consumer (agent_run_service.py) to match new constructor**
- **Found during:** Task 1
- **Issue:** Plan did not explicitly include agent_run_service.py consumer update, but the constructor signature change breaks the call site
- **Fix:** Updated BohriumSetupService construction to use functools.partial-bound callables
- **Files modified:** src/services/agent_run_service.py
- **Verification:** No import errors; callback injection tests pass
- **Committed in:** f453f9b6 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Essential to maintain runnable main execution path. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- matmaster/ no longer has any runtime import from src or evomaster.env.bohrium
- Phase 28 Plan 03 can proceed with the remaining consumer migration path (agent_run_service.py full matmaster-native entry point)
- The only remaining xfail in import_audit is for evomaster.agent.session (bash_tool.py), which is tracked for a future phase

## Self-Check: PASSED

All 8 files verified present. All 3 commit hashes verified in git log.

---
*Phase: 28-src-consumer*
*Completed: 2026-04-01*
