---
phase: 06-service-layer-wiring
plan: 02
subsystem: api
tags: [direct-exp, builtin-tools, worker-registry, adapter, service-cleanup]

# Dependency graph
requires:
  - phase: 06-service-layer-wiring
    provides: PlaygroundContext with session and config_dir fields, _build_llm_provider factory
  - phase: 03-exp-assembly-layer
    provides: DirectExp, ToolRegistry, EvoToolAdapter, Guard Protocol
provides:
  - DirectExp with clean constructor (no session/config_dir/builtin_tools params)
  - _init_builtin_tools constructing BashTool/EditorTool/MonitorJobTool from ctx.session
  - WorkerRegistryServiceAdapter bridging Service -> Protocol (None -> bool for delete)
  - Clean service layer DirectExp construction without stubs or hasattr hacks
affects: [06-service-layer-wiring, service-layer, assembly]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Builtin tools constructed inside assemble() from ctx.session, not passed via constructor"
    - "Adapter pattern bridging return type mismatch (None -> bool) between service and Protocol"
    - "EvoToolAdapter wraps BashTool/EditorTool/MonitorJobTool for matmaster Tool Protocol"

key-files:
  created:
    - src/services/worker_registry_adapter.py
  modified:
    - matmaster/assembly/direct_exp.py
    - matmaster/assembly/guards.py
    - matmaster/assembly/__init__.py
    - src/services/agent_run_service.py
    - tests/matmaster/assembly/test_direct_exp.py
    - tests/matmaster/assembly/test_guard_injection.py
    - tests/matmaster/assembly/test_worker_registry.py
    - tests/matmaster/integration/test_e2e_mat_master.py
    - tests/matmaster/integration/test_e2e_minimal.py
    - tests/matmaster/integration/test_pipeline_alignment.py
    - tests/matmaster/integration/test_quota_pipeline.py

key-decisions:
  - "Builtin tools constructed in _init_builtin_tools from ctx.session, not passed externally"
  - "Guard shells (ManuscriptGateGuard, AuthFailureGateGuard) fully removed, not replaced with new guards"
  - "WorkerRegistryServiceAdapter converts delete_session_run_owner None->True for Protocol compliance"
  - "Service layer DirectExp construction cleaned: no builtin_tools, session, config_dir, hasattr"

patterns-established:
  - "ctx.session as single source of session for all tool adapters (builtin, skill, MCP)"
  - "Adapter pattern for bridging return type mismatches between existing services and new Protocols"

requirements-completed: [ASBL-02, ASBL-06, MIGR-01, MIGR-02]

# Metrics
duration: 11min
completed: 2026-03-22
---

# Phase 6 Plan 2: DirectExp Cleanup + WorkerRegistry Adapter Summary

**DirectExp constructor cleaned of session/builtin_tools/config_dir params with builtin tools constructed in assemble() from ctx.session, WorkerRegistryServiceAdapter bridging Service->Protocol, and all service layer stubs removed**

## Performance

- **Duration:** 11 min
- **Started:** 2026-03-22T13:29:08Z
- **Completed:** 2026-03-22T13:40:55Z
- **Tasks:** 2
- **Files modified:** 12

## Accomplishments
- DirectExp constructor cleaned: session, config_dir, builtin_tools parameters removed; builtin tools (BashTool, EditorTool, MonitorJobTool) now constructed inside assemble() from ctx.session via _init_builtin_tools
- ManuscriptGateGuard and AuthFailureGateGuard shell implementations removed from guards.py; guard injection mechanism preserved via DirectExp(guards=[...])
- WorkerRegistryServiceAdapter created to bridge WorkerRegistryService (returns None from delete) to WorkerRegistry Protocol (requires bool)
- Service layer fully cleaned: no _get_builtin_tools method, no hasattr hacks, no old constructor params
- All 406 matmaster tests pass including 9 new tests

## Task Commits

Each task was committed atomically:

1. **Task 1: DirectExp constructor cleanup + builtin tool construction in assemble()** - `ed64e1a` (feat)
2. **Task 2: WorkerRegistry adapter + service layer cleanup** - `085171c` (feat)

## Files Created/Modified
- `matmaster/assembly/direct_exp.py` - Removed session/config_dir/builtin_tools params; added _init_builtin_tools constructing tools from ctx.session
- `matmaster/assembly/guards.py` - Removed ManuscriptGateGuard and AuthFailureGateGuard shell classes
- `matmaster/assembly/__init__.py` - Removed guard shell exports from __all__
- `src/services/worker_registry_adapter.py` - NEW: WorkerRegistryServiceAdapter bridging Service -> Protocol
- `src/services/agent_run_service.py` - Removed _get_builtin_tools method; cleaned DirectExp construction (no old params, no hasattr)
- `tests/matmaster/assembly/test_direct_exp.py` - Updated existing tests; added TestDirectExpBuiltinTools (4 new tests)
- `tests/matmaster/assembly/test_guard_injection.py` - Updated to use inline stub guards instead of removed guard classes
- `tests/matmaster/assembly/test_worker_registry.py` - Added TestWorkerRegistryServiceAdapter (5 new tests)
- `tests/matmaster/integration/test_e2e_mat_master.py` - Removed builtin_tools and _get_builtin_tools references
- `tests/matmaster/integration/test_e2e_minimal.py` - Removed builtin_tools reference
- `tests/matmaster/integration/test_pipeline_alignment.py` - Removed builtin_tools reference
- `tests/matmaster/integration/test_quota_pipeline.py` - Removed _get_builtin_tools mocks

## Decisions Made
- Builtin tools constructed in _init_builtin_tools from ctx.session: all three tools (BashTool, EditorTool, MonitorJobTool) wrapped with EvoToolAdapter and registered as source="builtin"
- Guard shells fully removed rather than replaced: guard injection mechanism via DirectExp(guards=[...]) and GuardPipeline remains available; future business guards should be Hooks not Guards
- WorkerRegistryServiceAdapter uses simple delegation with None->True conversion for delete_session_run_owner, matching the Protocol's bool return requirement
- Service layer cleanup done in Task 1 (partially) to unblock tests, completed in Task 2

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed assembly/__init__.py importing removed guard classes**
- **Found during:** Task 1 (guard shell removal)
- **Issue:** matmaster/assembly/__init__.py imported AuthFailureGateGuard and ManuscriptGateGuard which were removed from guards.py
- **Fix:** Removed the guard imports and exports from __init__.py __all__
- **Files modified:** matmaster/assembly/__init__.py
- **Verification:** All imports resolve, all tests pass
- **Committed in:** ed64e1a (Task 1 commit)

**2. [Rule 3 - Blocking] Updated test_guard_injection.py referencing removed guards**
- **Found during:** Task 1 (guard shell removal)
- **Issue:** tests/matmaster/assembly/test_guard_injection.py imported and tested ManuscriptGateGuard and AuthFailureGateGuard
- **Fix:** Replaced with inline _StubGuard class satisfying Guard Protocol; preserved guard injection mechanism tests
- **Files modified:** tests/matmaster/assembly/test_guard_injection.py
- **Verification:** All guard injection tests pass
- **Committed in:** ed64e1a (Task 1 commit)

**3. [Rule 3 - Blocking] Updated E2E tests using removed DirectExp constructor params**
- **Found during:** Task 1 (constructor cleanup)
- **Issue:** test_e2e_mat_master.py, test_e2e_minimal.py, test_pipeline_alignment.py used builtin_tools= parameter
- **Fix:** Removed builtin_tools param; register tools via spec.tool_registry.register() after assemble()
- **Files modified:** tests/matmaster/integration/test_e2e_mat_master.py, test_e2e_minimal.py, test_pipeline_alignment.py
- **Verification:** All E2E tests pass
- **Committed in:** ed64e1a (Task 1 commit)

**4. [Rule 3 - Blocking] Pre-fixed service layer DirectExp construction to unblock tests**
- **Found during:** Task 1 (constructor cleanup)
- **Issue:** src/services/agent_run_service.py used builtin_tools=, session=, config_dir= params that were removed
- **Fix:** Cleaned DirectExp construction (planned for Task 2 but needed immediately for Task 1 tests to pass)
- **Files modified:** src/services/agent_run_service.py
- **Verification:** run_agent_sync E2E test passes
- **Committed in:** ed64e1a (Task 1 commit)

---

**Total deviations:** 4 auto-fixed (4 blocking issues)
**Impact on plan:** All auto-fixes were necessary consequences of removing constructor parameters. No scope creep. Task 2 service cleanup was partially done in Task 1 to maintain test suite integrity.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Service layer wiring is complete: all stubs removed, all Protocols bridged, all hasattr hacks eliminated
- Phase 6 fully done: LLM factory (Plan 1) + DirectExp cleanup + WorkerRegistry adapter (Plan 2)
- Ready for Phase 7 or milestone verification

## Self-Check: PASSED

All 12 files verified present. Both task commits (ed64e1a, 085171c) confirmed in git log. 406/406 tests passing.

---
*Phase: 06-service-layer-wiring*
*Completed: 2026-03-22*
</content>
</invoke>