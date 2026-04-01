---
phase: 29-main-execution-path
plan: 02
subsystem: decoupling
tags: [playground-deletion, evaluation-deletion, config-migration, archive, cleanup]

# Dependency graph
requires:
  - phase: 29-main-execution-path
    plan: 01
    provides: workspace_resolver migration, zero evomaster imports in matmaster/
provides:
  - Physical removal of playground/, evaluation/, run.py legacy code
  - Archived playground skills in .archive/playground-skills/
  - Config files pointing to ./workspace instead of ./playground/mat_master/workspace
  - Clean pyproject.toml without playground/evaluation references
affects: [30-audit]

# Tech tracking
tech-stack:
  added: []
  patterns: [workspace-as-session-root]

key-files:
  created:
    - workspace/.gitkeep
  modified:
    - .gitignore
    - configs/mat_master/config.yaml
    - matmaster_config/config.yaml
    - pyproject.toml

key-decisions:
  - "Removed stale !playground/mat_master/frontend/src/lib/ exception from .gitignore (Rule 2 - dead reference cleanup)"
  - "Archive contains 19 entries (18 skills + _common) matching actual directory content"

patterns-established:
  - "Session working_dir defaults to ./workspace at project root level"

requirements-completed: [CONS-01, CONS-02]

# Metrics
duration: 4min
completed: 2026-04-01
---

# Phase 29 Plan 02: Legacy Code Deletion, Skill Archive, and Config Path Migration Summary

**Deleted playground/ evaluation/ run.py (78K+ lines), archived 19 playground skills, migrated all config paths from playground/mat_master/workspace to ./workspace**

## Performance

- **Duration:** 4 min
- **Started:** 2026-04-01T16:19:34Z
- **Completed:** 2026-04-01T16:23:34Z
- **Tasks:** 2
- **Files modified:** 384 (381 deleted + 3 config/build files updated)

## Accomplishments
- Physically removed all legacy code: playground/ (310+ files), evaluation/ (80+ files), run.py, 5 orphaned test files, 2 test directories
- Archived 19 playground skill directories to .archive/playground-skills/ with .gitignore exclusion
- Migrated session.local.working_dir, docker.volumes, and dynamic_skills_root in both config files to ./workspace
- Cleaned pyproject.toml: removed playground CLI script, removed evaluation and playground from hatch packages
- 17 import audit + workspace resolver tests pass, pytest collection clean (no playground/evaluation ModuleNotFoundError)

## Task Commits

Each task was committed atomically:

1. **Task 1: Archive playground skills and delete legacy directories** - `67c5217d` (chore)
2. **Task 2: Update config files and pyproject.toml** - `05ad3b9c` (chore)

## Files Created/Modified
- `.archive/playground-skills/` - Archived 19 skill directories from playground/mat_master/skills/
- `.gitignore` - Added .archive/ exclusion, removed stale playground frontend lib exception
- `workspace/.gitkeep` - Created workspace directory as new session working_dir target
- `configs/mat_master/config.yaml` - working_dir, volumes, dynamic_skills_root migrated to ./workspace
- `matmaster_config/config.yaml` - working_dir, volumes migrated to ./workspace
- `pyproject.toml` - Removed playground CLI script entry, removed evaluation/playground from hatch packages

## Decisions Made
- Removed the `!playground/mat_master/frontend/src/lib/` exception from .gitignore since the playground directory no longer exists (dead gitignore rule cleanup)
- Archive contains 19 entries (18 skill directories + _common) matching actual playground skills content -- plan estimated 20 but actual count is 19

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Removed stale .gitignore playground exception**
- **Found during:** Task 1 (.gitignore reading)
- **Issue:** Line 24 had `!playground/mat_master/frontend/src/lib/` which negated the `lib/` ignore for a path inside the now-deleted playground/ directory
- **Fix:** Removed the stale exception line
- **Files modified:** .gitignore
- **Verification:** No playground references remain in .gitignore except the archive comment
- **Committed in:** 67c5217d (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 missing critical)
**Impact on plan:** Minor cleanup of dead .gitignore rule. No scope creep.

## Issues Encountered
- pytest collection shows 2 pre-existing errors (test_chat_session_list.py, test_openapi_chat_docs.py) due to read-only filesystem in worktree environment -- unrelated to this plan's changes
- Archive entry count is 19 (not 20 as plan estimated) -- the plan counted "_common + 19 skills" but actual directory has 18 skills + _common = 19 entries

## User Setup Required
None - no external service configuration required.

## Known Stubs
None - this plan is a deletion/cleanup plan with no new code.

## Next Phase Readiness
- Repository has zero playground/, evaluation/, run.py code
- All config paths point to matmaster-native locations (./workspace)
- pyproject.toml references only evomaster, matmaster, utils
- Phase 30 (audit and independence proof) can proceed with clean codebase

## Self-Check: PASSED

All 6 created/modified files verified present. Both task commits (67c5217d, 05ad3b9c) confirmed in git log. All 3 deleted targets (playground/, evaluation/, run.py) verified absent.

---
*Phase: 29-main-execution-path*
*Completed: 2026-04-01*
