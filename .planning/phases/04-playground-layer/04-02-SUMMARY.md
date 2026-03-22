---
phase: 04-playground-layer
plan: 02
subsystem: playground
tags: [yaml-config, config-path, archival, cache-dir, integration-test]

# Dependency graph
requires:
  - phase: 04-playground-layer
    provides: Unified Playground class with prepare()/cleanup() lifecycle (plan 04-01)
provides:
  - playground: block in configs/mat_master/config.yaml with archival enabled
  - playground: block in configs/minimal/config.yaml with archival disabled
  - Config-driven cache path resolution via playground.cache_dir
  - Config-path compatibility tests proving unified Playground across both deployment shapes
affects: [04-03, 05-integration-quality]

# Tech tracking
tech-stack:
  added: []
  patterns: [config-driven-cache-path, workspace-sync-before-session-open]

key-files:
  created:
    - tests/matmaster/playground/test_playground_config_paths.py
  modified:
    - configs/mat_master/config.yaml
    - configs/minimal/config.yaml
    - matmaster/playground/playground.py

key-decisions:
  - "_build_archival_config() returns WorkspaceArchivalConfig even when enabled=False -- None only when no archival block exists in YAML"
  - "Workspace resolution and session config sync happen BEFORE session.open() to avoid /workspace mkdir on real configs"
  - "_resolve_cache_area() resolves relative playground.cache_dir under workspace path"

patterns-established:
  - "Config-driven cache path: playground.cache_dir in YAML controls cache area location"
  - "Session sync before open: workspace path synced to session config before session.open() to prevent default path side effects"

requirements-completed: [WKSP-02, WKSP-03]

# Metrics
duration: 5min
completed: 2026-03-22
---

# Phase 4 Plan 02: Config-Path Compatibility Summary

**Real mat_master and minimal YAML configs wired with playground archival/cache blocks, proven against unified Playground with 4 config-path integration tests**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-22T06:14:10Z
- **Completed:** 2026-03-22T06:19:25Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Added playground: config blocks to both real YAML configs (mat_master with archival enabled, minimal with archival disabled)
- Implemented config-driven cache path resolution via playground.cache_dir
- Fixed prepare() ordering: workspace sync before session.open() to prevent /workspace mkdir error on real configs
- 4 config-path integration tests proving unified Playground works with both deployment shapes
- Total 30 tests passing across Playground wave (14 contract + 12 lifecycle + 4 config-path)

## Task Commits

Each task was committed atomically:

1. **Task 1: Add real config blocks for unified Playground compatibility** - `43c6be6` (feat)
2. **Task 2: Add config-path compatibility tests for mat_master and minimal** - `25ec7be` (test)

_Note: Both tasks followed TDD flow (RED -> GREEN)_

## Files Created/Modified
- `configs/mat_master/config.yaml` - Added playground: block with cache_dir and archival (enabled, oss_bucket, oss_prefix, credential_ref)
- `configs/minimal/config.yaml` - Added playground: block with cache_dir and archival (disabled)
- `matmaster/playground/playground.py` - Reordered prepare() for workspace-first sync, added _resolve_cache_area(), updated _build_archival_config() to preserve disabled archival configs
- `tests/matmaster/playground/test_playground_config_paths.py` - 4 integration tests: mat_master path, minimal path, mat_master cache_dir, minimal cache_dir

## Decisions Made
- _build_archival_config() now returns the WorkspaceArchivalConfig object even when enabled=False. Previously it returned None for disabled archival, but the plan requires downstream code to be able to inspect archival settings regardless of enabled state. None is returned only when no archival block exists in the YAML at all.
- Workspace resolution and session config sync moved before session.open() in prepare(). The real configs have working_dir values that don't match the test workspace, and session.open() triggers env.setup() which creates the workspace directory. Syncing first prevents attempting to create /workspace on read-only filesystem.
- _resolve_cache_area() resolves relative playground.cache_dir under the workspace path, producing paths like workspace/.cache/matmaster.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed prepare() ordering: workspace sync before session.open()**
- **Found during:** Task 1 (TDD RED phase)
- **Issue:** session.open() calls env.setup() which tries to mkdir the workspace_path from config YAML (e.g., /workspace). On real configs, this fails with OSError: Read-only file system because the original workspace_path hasn't been updated yet.
- **Fix:** Moved workspace resolution and _sync_workspace_to_session_config() before session.open() so the session config has the correct path when env.setup() runs.
- **Files modified:** matmaster/playground/playground.py
- **Verification:** Both real config paths load and prepare() succeeds
- **Committed in:** 43c6be6 (Task 1 commit)

**2. [Rule 1 - Bug] Updated _build_archival_config() to preserve disabled archival configs**
- **Found during:** Task 1 (aligning with plan requirements)
- **Issue:** Plan requires ctx.archival is not None even when enabled=False for minimal config. Previous implementation returned None for any disabled archival.
- **Fix:** Removed the `if not cfg.enabled: return None` guard. Now returns the config object whenever an archival block exists in YAML, regardless of enabled state.
- **Files modified:** matmaster/playground/playground.py
- **Verification:** test_minimal_config_path asserts ctx.archival is not None and ctx.archival.enabled is False
- **Committed in:** 43c6be6 (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (2 bugs)
**Impact on plan:** Both fixes required for correct behavior with real config files. No scope creep.

## Issues Encountered
None beyond the auto-fixed deviations documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Both real config paths proven against unified Playground
- Archival config available in PlaygroundContext for both mat_master (enabled) and minimal (disabled)
- Config-driven cache paths working for both deployment shapes
- Ready for Plan 04-03: DirectExp capability ownership migration
- Ready for Phase 5: Service layer integration with real config paths

## Self-Check: PASSED

All files verified present. All commit hashes verified in git log.

---
*Phase: 04-playground-layer*
*Completed: 2026-03-22*
