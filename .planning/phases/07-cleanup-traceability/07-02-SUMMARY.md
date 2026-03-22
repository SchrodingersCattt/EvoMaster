---
phase: 07-cleanup-traceability
plan: 02
subsystem: infra
tags: [directory-restructure, import-rewrite, package-layout]

# Dependency graph
requires:
  - phase: 07-01
    provides: QueueBridge cleanup, bus/__init__.py simplified
provides:
  - matmaster/core/ package (agent, exp, direct_exp, guard_pipeline, hooks, context_builder, bus, playground)
  - matmaster/tools/ package (tool_registry, evomaster_tool_adapter)
  - matmaster/types/messages.py (Message types moved from engine/types.py)
  - matmaster/types/worker_registry.py (WorkerRegistry Protocol moved from assembly/)
  - Clean import paths across entire codebase (zero old path references)
affects: [all-future-development, migration-guide]

# Tech tracking
tech-stack:
  added: []
  patterns: ["core/ for runtime components", "tools/ for tool domain", "types/ for contracts and protocols"]

key-files:
  created:
    - matmaster/core/__init__.py
    - matmaster/tools/__init__.py
    - tests/matmaster/core/__init__.py
    - tests/matmaster/tools/__init__.py
  modified:
    - matmaster/types/__init__.py
    - matmaster/hooks/__init__.py
    - src/services/agent_run_service.py
    - src/services/chat_history.py

key-decisions:
  - "Merged assembly conftest.py (MockTool) into tools/conftest.py since test_tool_registry.py is its only consumer"
  - "Updated guards shell test to verify module deletion rather than attribute absence"
  - "Lazy import __getattr__ pattern preserved in core/__init__.py for Exp/DirectExp circular import avoidance"

patterns-established:
  - "core/ package: runtime core components with lazy Exp/DirectExp exports"
  - "tools/ package: tool registration and EvoMaster adaptation"
  - "types/ package: all contracts, protocols, message types, worker registry"

requirements-completed: [EBUS-02]

# Metrics
duration: 14min
completed: 2026-03-22
---

# Phase 7 Plan 2: Directory Restructure Summary

**Merged engine/, assembly/, bus/, playground/ into core/, tools/, types/ with full import rewrite across 66 files; all 380 tests pass**

## Performance

- **Duration:** 14 min
- **Started:** 2026-03-22T14:46:57Z
- **Completed:** 2026-03-22T15:01:29Z
- **Tasks:** 2
- **Files modified:** 66

## Accomplishments
- Eliminated 4 single-file/redundant packages (engine/, assembly/, bus/, playground/) into 2 new packages (core/, tools/) and expanded types/
- Rewrote all import paths across matmaster/, tests/, and src/services/ -- zero references to old paths remain
- All 380 tests pass with new import structure (up from expected 342 due to additional tests added in earlier phases)

## Task Commits

Each task was committed atomically:

1. **Task 1: Move source files and create new package __init__.py files** - `756883a` (refactor)
2. **Task 2: Restructure test directories and rewrite test imports** - `b7de88f` (refactor)

## Files Created/Modified

Key structural changes:
- `matmaster/core/__init__.py` - New runtime core package with lazy Exp/DirectExp exports
- `matmaster/tools/__init__.py` - New tool domain package
- `matmaster/types/__init__.py` - Updated with messages and worker_registry exports
- `matmaster/core/agent.py` - Moved from engine/agent.py, imports rewritten
- `matmaster/core/bus.py` - Moved from bus/queue.py
- `matmaster/core/playground.py` - Moved from playground/playground.py
- `matmaster/types/messages.py` - Moved from engine/types.py
- `matmaster/types/worker_registry.py` - Moved from assembly/worker_registry.py
- `matmaster/tools/tool_registry.py` - Moved from assembly/tool_registry.py
- `src/services/agent_run_service.py` - All matmaster imports updated

Deleted:
- `matmaster/engine/` directory (4 files)
- `matmaster/assembly/` directory (8 files including guards.py empty shell)
- `matmaster/bus/` directory (2 files)
- `matmaster/playground/` directory (2 files)
- `tests/matmaster/engine/` directory
- `tests/matmaster/assembly/` directory
- `tests/matmaster/bus/` directory
- `tests/matmaster/playground/` directory

## Decisions Made
- Merged assembly conftest.py (MockTool) into tools/conftest.py since test_tool_registry.py is its only consumer via relative import
- Updated guards shell test to verify module deletion (ImportError) rather than attribute absence since guards.py was completely deleted
- Preserved lazy import __getattr__ pattern in core/__init__.py for Exp/DirectExp to avoid the circular import chain (core.exp -> core.agent -> types.runtime -> tools.tool_registry)
- Fixed test_llm_provider.py reference to engine conftest -> core conftest (discovered via grep, Rule 1 auto-fix)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test_llm_provider.py stale conftest reference**
- **Found during:** Task 2 (test import rewrite)
- **Issue:** tests/matmaster/types/test_llm_provider.py imported `from tests.matmaster.engine.conftest import MockLLMProvider` which would fail after engine/ deletion
- **Fix:** Changed to `from tests.matmaster.core.conftest import MockLLMProvider`
- **Files modified:** tests/matmaster/types/test_llm_provider.py
- **Committed in:** b7de88f

**2. [Rule 1 - Bug] Fixed patch targets using old module paths in test_exp.py**
- **Found during:** Task 2 (test import rewrite)
- **Issue:** 5 instances of `patch("matmaster.engine.agent.AgentKernel")` would fail since the module moved
- **Fix:** Changed all to `patch("matmaster.core.agent.AgentKernel")`
- **Files modified:** tests/matmaster/core/test_exp.py
- **Committed in:** b7de88f

**3. [Rule 1 - Bug] Updated guards test for deleted module**
- **Found during:** Task 2 (test import rewrite)
- **Issue:** test_direct_exp.py tried `import matmaster.assembly.guards` which no longer exists
- **Fix:** Changed test to verify ImportError is raised when attempting to import the deleted module
- **Files modified:** tests/matmaster/core/test_direct_exp.py
- **Committed in:** b7de88f

---

**Total deviations:** 3 auto-fixed (3 bug fixes)
**Impact on plan:** All auto-fixes were necessary to prevent test failures from stale references. No scope creep.

## Issues Encountered
None

## Known Stubs
None - this plan is pure restructuring with no new functionality.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 7 (final phase) is now complete -- all plans executed
- Directory structure matches the D-04 target layout exactly
- All 380 tests pass under the new matmaster/ package organization
- No remaining references to old import paths anywhere in the codebase

## Self-Check: PASSED

- matmaster/core/__init__.py: FOUND
- matmaster/tools/__init__.py: FOUND
- matmaster/types/messages.py: FOUND
- matmaster/types/worker_registry.py: FOUND
- 07-02-SUMMARY.md: FOUND
- Commit 756883a (Task 1): FOUND
- Commit b7de88f (Task 2): FOUND
- All 380 tests: PASSED
- Old import references: ZERO

---
*Phase: 07-cleanup-traceability*
*Completed: 2026-03-22*
