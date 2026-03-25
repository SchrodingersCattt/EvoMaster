---
phase: 03-exp-assembly-layer
plan: 04
subsystem: assembly
tags: [circular-import, lazy-import, TYPE_CHECKING, __getattr__]

# Dependency graph
requires:
  - phase: 03-exp-assembly-layer (plan 03)
    provides: Exp base class, DirectExp, Guards with top-level AgentKernel import causing circular dependency
provides:
  - Circular import between assembly and engine fully resolved
  - All engine/bus/types test suites unblocked and collecting
  - Lazy import pattern for Exp/DirectExp in assembly __init__
affects: [04-playground-layer, 05-integration-quality]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "TYPE_CHECKING guard + lazy import inside method body for cross-layer dependencies"
    - "Module-level __getattr__ for lazy package exports that have heavy transitive imports"

key-files:
  created: []
  modified:
    - matmaster/assembly/exp.py
    - matmaster/assembly/__init__.py
    - tests/matmaster/assembly/test_exp.py

key-decisions:
  - "Combined TYPE_CHECKING block (for type annotations) + lazy import in run() body (for runtime) to break circular import in exp.py"
  - "Module-level __getattr__ in assembly/__init__.py to lazy-load Exp/DirectExp while preserving full __all__ public API"
  - "Test mock target changed from assembly.exp.AgentKernel to engine.agent.AgentKernel to match lazy import resolution path"

patterns-established:
  - "TYPE_CHECKING + lazy import: Use from __future__ import annotations + if TYPE_CHECKING block for type hints, plus local import inside method body for runtime usage when cross-layer imports would create cycles"
  - "Module __getattr__ lazy export: When a package __init__.py re-exports symbols that trigger heavy import chains, use __getattr__ to defer loading until first access"

requirements-completed: [ASBL-01, ASBL-03, ASBL-04]

# Metrics
duration: 2min
completed: 2026-03-22
---

# Phase 3 Plan 04: Gap Closure -- Circular Import Fix Summary

**Lazy import pattern in exp.py + __getattr__ in assembly/__init__.py breaks the circular import chain (assembly -> exp -> engine.agent) that blocked all engine/bus/types test collection**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-22T03:25:15Z
- **Completed:** 2026-03-22T03:27:20Z
- **Tasks:** 1
- **Files modified:** 3

## Accomplishments
- Broke the circular import chain: engine -> types -> assembly.__init__ -> direct_exp -> exp -> engine.agent
- All 236 matmaster tests pass (205 engine/assembly/bus/types + 31 others), 9 pre-existing TestAgentRuntimeSpec failures remain deferred to Phase 5
- Full import chain verified: matmaster.engine, matmaster.types, matmaster.assembly all import cleanly
- Public API preserved: `from matmaster.assembly import DirectExp, Exp, ToolRegistry` still works via lazy loading

## Task Commits

Each task was committed atomically:

1. **Task 1: Fix circular import in exp.py and __init__.py** - `a932de6` (fix)

## Files Created/Modified
- `matmaster/assembly/exp.py` - Added `from __future__ import annotations`, `TYPE_CHECKING` block for AgentKernel type hint, lazy import inside run() body
- `matmaster/assembly/__init__.py` - Removed eager Exp/DirectExp imports, added `__getattr__` lazy loader, kept all 8 symbols in `__all__`
- `tests/matmaster/assembly/test_exp.py` - Updated mock patch target from `matmaster.assembly.exp.AgentKernel` to `matmaster.engine.agent.AgentKernel`

## Decisions Made
- Combined TYPE_CHECKING block (for static analysis / type annotations) with lazy import in run() method body (for runtime) -- this is the standard Python pattern for breaking circular imports while preserving type safety
- Used module-level `__getattr__` (PEP 562) in assembly/__init__.py rather than removing Exp/DirectExp from exports -- preserves full public API without triggering the circular chain during package init
- Updated test mock target to patch at the definition site (engine.agent.AgentKernel) rather than the now-nonexistent import site -- matches Python mock best practice for lazy imports

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test mock patch target for lazy import**
- **Found during:** Task 1 (verification step)
- **Issue:** `tests/matmaster/assembly/test_exp.py` patched `matmaster.assembly.exp.AgentKernel` but after moving to lazy import, AgentKernel is no longer a module-level attribute of exp.py
- **Fix:** Changed patch target to `matmaster.engine.agent.AgentKernel` (the definition site where the lazy import resolves)
- **Files modified:** `tests/matmaster/assembly/test_exp.py`
- **Verification:** Both test_run_calls_assemble_then_kernel and test_assemble_kwargs_forwarded pass
- **Committed in:** a932de6 (part of task commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix)
**Impact on plan:** Necessary adaptation -- mock target must match import resolution path. No scope creep.

## Issues Encountered
None beyond the test adaptation documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 3 circular import gap is fully closed: all 11 observable truths now verified
- Engine, bus, types, and assembly test suites all collect and pass without ImportError
- Phase 4 (Playground Layer) can proceed with clean cross-layer imports
- Phase 5 (Integration Quality) has 9 pre-existing TestAgentRuntimeSpec test failures to address (object() as tool_registry)

## Self-Check: PASSED

All files verified present. Commit a932de6 verified in git log.

---
*Phase: 03-exp-assembly-layer*
*Completed: 2026-03-22*
