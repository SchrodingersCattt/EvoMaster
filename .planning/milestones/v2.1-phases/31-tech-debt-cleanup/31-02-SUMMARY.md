---
phase: 31-tech-debt-cleanup
plan: 02
subsystem: testing
tags: [stale-tests, isolation-script, docstring-cleanup, requirements-traceability]

# Dependency graph
requires:
  - phase: 31-tech-debt-cleanup
    plan: 01
    provides: Session Protocol mock pattern, conftest MockLLMProvider
provides:
  - 9 stale test files deleted (evaluation/ and evomaster/ references)
  - Isolation script hardened for absent evomaster/ directory
  - job_service.py docstring references matmaster paths
  - 8 SUMMARY files have requirements_completed frontmatter
  - REQUIREMENTS.md all 19 checkboxes confirmed [x]
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Conditional directory moves in shell scripts: [ -d dir ] && mv"
    - "requirements_completed (underscore) as standard frontmatter field name"

key-files:
  created: []
  modified:
    - scripts/test_matmaster_isolation.sh
    - matmaster/adaptors/calculation/job_service.py
    - matmaster/core/playground.py

key-decisions:
  - "Normalized requirements-completed (hyphenated) to requirements_completed (underscored) across all 8 SUMMARY files for grep-based tooling consistency"
  - "Split shared requirement IDs across correct plan SUMMARYs: CONS-01 to 29-01, CONS-02 to 29-02, QUAL-06 to 30-01, QUAL-07 to 30-02"

patterns-established:
  - "requirements_completed field uses underscore separator for machine-readability"

requirements_completed: []

# Metrics
duration: 5min
completed: 2026-04-02
---

# Phase 31 Plan 02: Stale Test Deletion, Isolation Script Hardening, Documentation Sync

**Deleted 9 stale test files referencing deleted evaluation/evomaster dirs, hardened isolation script for absent directories, synced docstrings and SUMMARY frontmatter**

## Performance

- **Duration:** 5 min
- **Started:** 2026-04-02T04:02:02Z
- **Completed:** 2026-04-02T04:07:05Z
- **Tasks:** 2
- **Files modified:** 20 (9 deleted + 1 script + 2 source + 8 SUMMARYs)

## Accomplishments

- Deleted 9 stale test files: 2 devshell eval tests (evaluation/ refs), 1 evomaster agent test, 6 root-level tests importing deleted evomaster module (1,276 lines removed)
- Removed empty tests/evomaster/ directory tree
- Hardened isolation script with conditional `[ -d ] && mv` for both evomaster/ and src/ directories
- Fixed job_service.py docstring from evomaster path to matmaster/tools/builtin/monitor_job
- Cleaned playground.py comment removing "evomaster mixin" suffix
- Normalized `requirements-completed` to `requirements_completed` across 8 SUMMARY files
- Confirmed all 19 REQUIREMENTS.md v2.1 checkboxes are [x]

## Task Commits

Each task was committed atomically:

1. **Task 1: Delete stale test files and fix isolation script** - `5eabbe28` (fix)
2. **Task 2: Sync docstrings, SUMMARY frontmatter, and confirm REQUIREMENTS.md** - `f5e53a7d` (docs)

## Files Created/Modified

### Deleted
- `tests/matmaster/devshell/test_export_review_bundle.py` -- references deleted evaluation/ directory
- `tests/matmaster/devshell/test_run_devshell_eval_script.py` -- references deleted evaluation/ directory
- `tests/evomaster/agent/test_agent_context.py` -- imports deleted evomaster module
- `tests/test_evomaster_config_migration.py` -- imports deleted evomaster module
- `tests/test_builtin_tools_without_think.py` -- imports deleted evomaster module
- `tests/test_llm_reasoning_response.py` -- imports deleted evomaster module
- `tests/test_llm_reasoning_stream.py` -- imports deleted evomaster module
- `tests/test_llm_thinking_adapters.py` -- imports deleted evomaster module
- `tests/test_reasoning_state_roundtrip.py` -- imports deleted evomaster module
- `tests/evomaster/` -- empty directory tree removed

### Modified
- `scripts/test_matmaster_isolation.sh` -- conditional mv for absent evomaster/ and src/
- `matmaster/adaptors/calculation/job_service.py` -- docstring path updated to matmaster
- `matmaster/core/playground.py` -- comment cleaned of evomaster mixin reference
- 8 SUMMARY files -- requirements_completed frontmatter normalized and corrected

## Decisions Made

- Normalized all `requirements-completed` (hyphenated) fields to `requirements_completed` (underscored) for consistent grep/tooling compatibility
- Split previously-shared requirement IDs to their correct plan SUMMARYs (CONS-01 to 29-01 only, CONS-02 to 29-02 only, QUAL-06 to 30-01 only, QUAL-07 to 30-02 only)
- Added missing TOOL-07 to 26-01-SUMMARY (was tracking TOOL-08 and TOOL-10 but not TOOL-07)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Known Stubs

None - all functionality is fully wired (this plan is cleanup-only).

## Next Phase Readiness

- Phase 31 (tech-debt-cleanup) is complete: all stale tests deleted, isolation script hardened, documentation synced
- v2.1 milestone is fully closed: 19/19 requirements complete, all SUMMARY frontmatter populated, zero evomaster references in matmaster/ runtime
- Ready for v2.2 scope (LEGY-01, LEGY-02, PKG-01, HIST-01) when milestone transitions

---
*Phase: 31-tech-debt-cleanup*
*Completed: 2026-04-02*
