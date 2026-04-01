---
phase: 30-decoupling-audit
plan: 02
subsystem: decoupling
tags: [evomaster-deletion, skill-archive, pyproject, config-cleanup, isolation]

requires:
  - phase: 30-01
    provides: isolation test infrastructure, import audit with 24-violation allowlist
provides:
  - evomaster/ directory physically deleted (113 files, 26k lines removed)
  - 5 evomaster skills archived to .archive/evomaster-skills/
  - pyproject.toml packages reduced to ["matmaster", "utils"]
  - direct.toml skills_root cleaned to single matmaster path
  - config.yaml skills_root migrated from evomaster/skills to matmaster/skills/lazymcp
  - Import audit violations reduced from 24 to 1 (matmaster/core/__init__.py:L12)
affects: [30-03, decoupling-audit, matmaster-independence]

tech-stack:
  added: []
  patterns:
    - "gitignored .archive/ for local-only historical skill preservation"

key-files:
  created:
    - .archive/evomaster-skills/ (5 skill directories, gitignored)
  modified:
    - pyproject.toml
    - matmaster/exps/direct.toml
    - matmaster/skills/__init__.py
    - configs/mat_master/config.yaml
    - scripts/test_matmaster_isolation.sh
    - tests/matmaster/test_import_audit.py
    - tests/matmaster/adaptors/calculation/test_job_service.py

key-decisions:
  - "Import audit KNOWN_VIOLATIONS cleaned from 24 to 1 -- 23 violations were already resolved in prior phases"
  - "config.yaml user skill paths (~/.evomaster-skills) kept as-is -- filesystem path names, not package imports; deferred to v2.2"
  - "Pre-existing test failures (devshell, subagent_spawn, context) documented but not fixed -- out of plan scope"

patterns-established:
  - "Post-deletion config sweep: pyproject.toml + exp TOML + YAML configs + code comments checked for stale references"

requirements-completed: [QUAL-07]

duration: 9min
completed: 2026-04-02
---

# Phase 30 Plan 02: evomaster/ Deletion and Config Cleanup Summary

**Physically deleted evomaster/ (113 files, 26k lines), archived 5 skills, cleaned all configs to matmaster-only paths**

## Performance

- **Duration:** 9 min
- **Started:** 2026-04-01T17:39:14Z
- **Completed:** 2026-04-01T17:48:04Z
- **Tasks:** 2
- **Files modified:** 7 (+ 113 deleted)

## Accomplishments
- Deleted evomaster/ directory entirely (113 files, 26,359 lines of legacy code removed)
- Archived 5 evomaster skills (calculation, mcp-builder, pdf, rag, skill-creator) to .archive/evomaster-skills/
- Updated pyproject.toml packages from ["evomaster", "matmaster", "utils"] to ["matmaster", "utils"]
- Cleaned direct.toml skills_root: removed dead playground/mat_master/skills path
- Migrated config.yaml skills_root from evomaster/skills to matmaster/skills/lazymcp
- Updated import audit: reduced KNOWN_VIOLATIONS from 24 to 1 (only matmaster/core/__init__.py:L12 remains)
- Fixed stale test assertions in test_job_service.py (evomaster imports fully migrated)
- Updated isolation script to use --extra dev for pytest availability

## Task Commits

Each task was committed atomically:

1. **Task 1: Isolation test + archive + delete evomaster/** - `1e95d316` (feat)
2. **Task 2: Update configs and pyproject.toml** - `9b1b228a` (chore)

## Files Created/Modified
- `evomaster/` - Entire directory deleted (113 files)
- `.archive/evomaster-skills/` - 5 skill directories archived (gitignored, local only)
- `pyproject.toml` - Removed "evomaster" from hatch packages list
- `matmaster/exps/direct.toml` - skills_root reduced to single matmaster path
- `configs/mat_master/config.yaml` - skills_root changed to matmaster/skills/lazymcp
- `matmaster/skills/__init__.py` - Docstring updated to reflect current architecture
- `scripts/test_matmaster_isolation.sh` - Added --extra dev flag for pytest
- `tests/matmaster/test_import_audit.py` - KNOWN_VIOLATIONS reduced from 24 to 1
- `tests/matmaster/adaptors/calculation/test_job_service.py` - Replaced stale evomaster import assertion

## Decisions Made

- **Import audit cleanup:** 23 of 24 KNOWN_VIOLATIONS entries were already resolved in Phases 25-29. Cleaned the frozenset to only track the 1 remaining violation (matmaster/core/__init__.py:L12 -- playground import).
- **User skill paths kept as-is:** config.yaml `local_user_skills_root: "~/.evomaster-skills"` and `remote_user_skills_root: "/personal/.evomaster-skills"` are filesystem directory names, not package import paths. Deferred to v2.2 as optional naming cleanup.
- **Pre-existing test failures documented:** 25+ pre-existing test failures (devshell Pydantic validation, subagent_spawn API mismatch, PlaygroundContext session typing, compaction real API) were confirmed as not caused by Plan 30-02 changes. These exist both with and without evomaster/ present.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Stale test assertion in test_job_service.py**
- **Found during:** Task 1 (pre-deletion test run)
- **Issue:** test_evomaster_imports_are_function_level_only asserted that evomaster.env.bohrium lazy imports exist in job_service.py, but they were fully migrated to matmaster in prior phases
- **Fix:** Replaced assertion with test_no_evomaster_imports_remain that verifies no evomaster imports exist at all
- **Files modified:** tests/matmaster/adaptors/calculation/test_job_service.py
- **Verification:** Test passes; AST scan confirms 0 evomaster imports in job_service.py
- **Committed in:** 1e95d316 (Task 1 commit)

**2. [Rule 1 - Bug] Import audit KNOWN_VIOLATIONS contained 23 stale entries**
- **Found during:** Task 1 (pre-deletion test run)
- **Issue:** KNOWN_VIOLATIONS frozenset listed 24 entries from Plan 01, but 23 had already been resolved in Phases 25-29. Test failed on stale entries check.
- **Fix:** Reduced KNOWN_VIOLATIONS to 1 entry (matmaster/core/__init__.py:L12), updated expected count
- **Files modified:** tests/matmaster/test_import_audit.py
- **Verification:** test_no_forbidden_imports_in_matmaster passes; test_known_violations_count == 1
- **Committed in:** 1e95d316 (Task 1 commit)

**3. [Rule 3 - Blocking] Isolation script missing --extra dev flag**
- **Found during:** Task 1 (running isolation test)
- **Issue:** scripts/test_matmaster_isolation.sh used `uv run python -m pytest` which recreated venv without dev deps, causing "No module named pytest"
- **Fix:** Changed to `uv run --extra dev python -m pytest`
- **Files modified:** scripts/test_matmaster_isolation.sh
- **Verification:** Script runs successfully when invoked manually
- **Committed in:** 1e95d316 (Task 1 commit)

---

**Total deviations:** 3 auto-fixed (2 Rule 1 bugs, 1 Rule 3 blocking)
**Impact on plan:** All fixes necessary for test correctness. No scope creep.

## Issues Encountered
- Pre-existing test failures (25+) confirmed to exist both with and without evomaster/ present. These are unrelated to decoupling and need separate investigation.
- .archive/ directory is gitignored, so archived skills are local-only preservation. This is consistent with the project's archival pattern.

## Known Stubs
None - all artifacts are fully functional.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- evomaster/ physically gone, all configs point to matmaster paths
- Only 1 import violation remains: matmaster/core/__init__.py:L12 (playground import)
- Plan 30-03 can proceed with final verification and any remaining cleanup
- Pre-existing test failures should be addressed in a future maintenance phase

## Self-Check: PASSED

- FOUND: 30-02-SUMMARY.md
- FOUND: 1e95d316 (Task 1 commit)
- FOUND: 9b1b228a (Task 2 commit)
- FOUND: .archive/evomaster-skills/
- FOUND: evomaster/ deleted

---
*Phase: 30-decoupling-audit*
*Completed: 2026-04-02*
