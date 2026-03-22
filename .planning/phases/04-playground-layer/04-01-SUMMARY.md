---
phase: 04-playground-layer
plan: 01
subsystem: playground
tags: [pydantic, frozen-model, session, workspace, logging, lifecycle]

# Dependency graph
requires:
  - phase: 01-foundation-contracts
    provides: PlaygroundContext frozen model pattern
  - phase: 03-exp-assembly-layer
    provides: Exp.assemble(ctx) interface consuming PlaygroundContext
provides:
  - WorkspaceArchivalConfig frozen nested contract
  - PlaygroundContext without capability objects (mcp_manager/skill_registry removed)
  - Unified Playground class with prepare()/cleanup() two-phase lifecycle
  - Session ownership tracking (_owns_session) for injected session safety
affects: [04-02, 04-03, 05-integration-quality]

# Tech tracking
tech-stack:
  added: []
  patterns: [two-phase-lifecycle, session-ownership-tracking, config-driven-playground]

key-files:
  created:
    - matmaster/playground/__init__.py
    - matmaster/playground/playground.py
    - tests/matmaster/playground/__init__.py
    - tests/matmaster/playground/test_playground.py
  modified:
    - matmaster/types/context.py
    - tests/matmaster/types/test_context.py

key-decisions:
  - "WorkspaceArchivalConfig returns None when archival.enabled is False -- avoids exposing disabled archival metadata downstream"
  - "Playground uses ConfigManager directly with config_path constructor -- consistent with existing BasePlayground pattern"
  - "LocalSession created with LocalSessionConfig from config YAML for testing -- avoids Docker/SSH side effects in unit tests"

patterns-established:
  - "Two-phase lifecycle: prepare(run_meta) -> PlaygroundContext, cleanup() releases owned resources"
  - "Session ownership: _owns_session flag determines whether cleanup() closes the session"
  - "Config-driven session creation: session.type in YAML selects Local/Docker/SSH"

requirements-completed: [WKSP-01, WKSP-04]

# Metrics
duration: 5min
completed: 2026-03-22
---

# Phase 4 Plan 01: Playground Contract + Unified Core Lifecycle Summary

**Environment-only PlaygroundContext contract (mcp_manager/skill_registry removed, WorkspaceArchivalConfig added) and unified Playground with prepare()/cleanup() lifecycle, session ownership tracking, and config-driven workspace/logging setup**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-22T06:05:53Z
- **Completed:** 2026-03-22T06:10:38Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- Removed mcp_manager and skill_registry from PlaygroundContext, making it strictly environment-only
- Added WorkspaceArchivalConfig frozen nested contract with enabled/oss_bucket/oss_prefix/credential_ref fields
- Created unified Playground class with prepare()/cleanup() two-phase lifecycle
- Implemented session ownership tracking: injected sessions are never closed by Playground
- 26 tests passing (14 contract + 12 lifecycle)

## Task Commits

Each task was committed atomically:

1. **Task 1: Update the Playground layer contract and tests** - `cebabbb` (feat)
2. **Task 2: Create the unified Playground core lifecycle** - `55f120c` (feat)

_Note: Both tasks followed TDD flow (RED -> GREEN)_

## Files Created/Modified
- `matmaster/types/context.py` - PlaygroundContext contract: removed mcp_manager/skill_registry, added WorkspaceArchivalConfig and archival field
- `matmaster/playground/__init__.py` - Package init exporting Playground
- `matmaster/playground/playground.py` - Unified Playground class with prepare()/cleanup(), session creation, workspace resolution, logging setup, archival config building
- `tests/matmaster/types/test_context.py` - 14 tests: frozen behavior, archival defaults, roundtrip, removed fields assertion
- `tests/matmaster/playground/__init__.py` - Test package init
- `tests/matmaster/playground/test_playground.py` - 12 tests: prepare returns context, workspace paths, cache area, archival from config, log files, session ownership, cleanup behavior

## Decisions Made
- WorkspaceArchivalConfig._build_archival_config() returns None when archival.enabled is False, avoiding exposing disabled archival metadata to downstream layers
- Playground constructor takes config_path (str | Path), consistent with existing BasePlayground usage patterns
- Tests use minimal YAML configs with LocalSession only to avoid Docker/SSH infrastructure side effects
- _sync_workspace_to_session_config updates both workspace_path and working_dir fields to prevent directory inconsistency (pitfall from RESEARCH.md)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added required EvoMasterConfig fields to test YAML**
- **Found during:** Task 2 (test execution)
- **Issue:** Minimal test YAML config missing env.cluster/docker/scheduler required by EvoMasterConfig validation
- **Fix:** Added minimal env block with cluster/docker/scheduler to test config helper
- **Files modified:** tests/matmaster/playground/test_playground.py
- **Verification:** All 12 tests pass
- **Committed in:** 55f120c (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Test config helper needed required EvoMasterConfig fields. No scope creep.

## Issues Encountered
None beyond the test config validation fix documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- PlaygroundContext contract is locked: environment-only, no capability objects
- Unified Playground ready for config-path compatibility testing (Plan 04-02)
- Session ownership pattern ready for DirectExp capability migration (Plan 04-03)
- archival field available for Service layer workspace upload (Phase 5)

---
*Phase: 04-playground-layer*
*Completed: 2026-03-22*
