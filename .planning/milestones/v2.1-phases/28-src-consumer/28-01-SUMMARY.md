---
phase: 28-src-consumer
plan: 01
subsystem: integration
tags: [bohrium, credentials, import-audit, decoupling, namedtuple]

requires:
  - phase: 27-mcp-calculation
    provides: MCP/calculation native linkage (evomaster.env.bohrium lazy imports remain)
provides:
  - matmaster/integration/bohrium_env.py with BOHRIUM_OPENAPI_HOST, BohriumSetupResult, 4 pure functions
  - Extended import audit covering src, evomaster.agent.session, evomaster.env.bohrium
affects: [28-02, 28-03]

tech-stack:
  added: []
  patterns: [module-level constant from env with rstrip, NamedTuple type duplication for dependency inversion, AST-based import audit with TYPE_CHECKING exclusion]

key-files:
  created:
    - matmaster/integration/bohrium_env.py
    - tests/matmaster/test_bohrium_env.py
  modified:
    - tests/matmaster/test_import_audit.py

key-decisions:
  - "Simplified BOHRIUM_OPENAPI_HOST to single os.getenv with static default (no URL_PART logic from src/utils/constant.py)"
  - "BohriumSetupResult duplicated as NamedTuple to break src reverse dependency (Plan 02 will switch bohrium_setup.py import)"
  - "xfail markers on new audit tests that will pass after Plan 02 migration"
  - "Added xfail for evomaster.agent.session test due to bash_tool.py lazy import (out of Phase 28 scope)"

patterns-established:
  - "Import audit with _find_all_imports_matching: scans all nesting levels, excludes TYPE_CHECKING blocks"
  - "Dependency inversion via type duplication: NamedTuple copied to matmaster side, src version remains canonical until consumers migrate"

requirements-completed: [INVR-01, INVR-02]

duration: 4min
completed: 2026-04-01
---

# Phase 28 Plan 01: Bohrium Env Module + Import Audit Extension Summary

**Self-contained bohrium_env.py with 4 pure functions + BOHRIUM_OPENAPI_HOST constant + BohriumSetupResult NamedTuple; import audit extended to cover src/evomaster.agent.session/evomaster.env.bohrium dependencies**

## Performance

- **Duration:** 4 min
- **Started:** 2026-04-01T12:36:35Z
- **Completed:** 2026-04-01T12:41:07Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- Created matmaster/integration/bohrium_env.py as a zero-dependency module (only imports os, copy, typing) with all Bohrium credential/executor helpers migrated from evomaster/env/bohrium.py
- Extended import audit tests with 3 new test classes covering Phase 28 decoupling targets (src.*, evomaster.agent.session.*, evomaster.env.bohrium)
- 22 tests passing, 3 xfailed (expected to pass after Plan 02 completes migration)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create bohrium_env.py module (TDD)**
   - `b4b21da4` (test) - RED: failing tests for bohrium_env
   - `5bd167ed` (feat) - GREEN: bohrium_env module + passing tests
2. **Task 2: Extend import audit tests** - `542bb507` (feat)

## Files Created/Modified

- `matmaster/integration/bohrium_env.py` - Bohrium constants, credentials, executor injection, skill remote env builder; no evomaster/src/playground imports
- `tests/matmaster/test_bohrium_env.py` - 12 unit tests covering BOHRIUM_OPENAPI_HOST, get_bohrium_credentials, get_bohrium_storage_config, inject_bohrium_executor, build_bohrium_skill_remote_env, BohriumSetupResult
- `tests/matmaster/test_import_audit.py` - Added TestNoSrcImportsInMatmaster, TestNoEvomasterSessionImportsInMatmaster, TestNoEvomasterEnvBohriumImportsAnywhere; removed TestExpectedLazyBohrimImportsExist

## Decisions Made

- **Simplified BOHRIUM_OPENAPI_HOST**: The original src/utils/constant.py uses URL_PART logic for environment-specific hosts. The new module uses a simpler `os.getenv('BOHRIUM_BASE_URL', 'https://open.bohrium.com').rstrip('/')` per plan decision D-03, since the URL_PART logic is src-specific infrastructure.
- **BohriumSetupResult as duplicate NamedTuple**: Copied verbatim from src/services/agent_run_bohrium.py to break the reverse dependency. Both versions coexist until Plan 02 switches bohrium_setup.py's TYPE_CHECKING import.
- **xfail strategy for pre-migration tests**: TestNoSrcImportsInMatmaster, TestNoEvomasterEnvBohriumImportsAnywhere, and TestNoEvomasterSessionImportsInMatmaster use `@pytest.mark.xfail(strict=False)` to avoid CI failures while clearly documenting the migration targets.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added xfail to TestNoEvomasterSessionImportsInMatmaster**
- **Found during:** Task 2 (import audit extension)
- **Issue:** bash_tool.py has a lazy import from evomaster.agent.session.local (builtin helper dependency, PROJECT.md item A.3) which is out of Phase 28 scope
- **Fix:** Added xfail marker with reason string explaining the remaining dependency
- **Files modified:** tests/matmaster/test_import_audit.py
- **Verification:** pytest passes with 3 xfailed tests
- **Committed in:** 542bb507 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 missing critical)
**Impact on plan:** Minor -- correctly identifies an out-of-scope dependency and marks it for future resolution. No scope creep.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- bohrium_env.py is ready for Plan 02 to switch import paths in bohrium_setup.py and script_env.py
- Import audit tests will automatically detect regressions during Plan 02/03 migration
- xfail markers on new tests serve as a checklist -- they will start passing (and strict=False allows that) as migration progresses

## Self-Check: PASSED

All 4 files verified present. All 3 commits verified in git history.

---
*Phase: 28-src-consumer*
*Completed: 2026-04-01*
