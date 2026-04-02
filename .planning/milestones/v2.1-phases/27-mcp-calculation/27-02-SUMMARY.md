---
phase: 27-mcp-calculation
plan: 02
subsystem: adaptors
tags: [calculation, bohrium, oss, mcp, lazy-import, path-adaptor, job-service]

# Dependency graph
requires:
  - phase: 26-tool
    provides: tool internalization patterns and lazy import strategy (D-08)
provides:
  - matmaster/adaptors/calculation/ package with env_config, oss_io, job_service, path_adaptor
  - CalculationPathAdaptor with 4-layer path detection + model alias + OSS upload
  - Bohrium OpenAPI job service (query/download/terminate)
  - OSS file upload/download for calculation MCP tools
affects: [27-mcp-calculation plan 03, lazy_mcp, tools/builtin/monitor_job]

# Tech tracking
tech-stack:
  added: []
  patterns: [function-level lazy import for evomaster.env.bohrium, duck-typing for session type detection]

key-files:
  created:
    - matmaster/adaptors/__init__.py
    - matmaster/adaptors/calculation/__init__.py
    - matmaster/adaptors/calculation/env_config.py
    - matmaster/adaptors/calculation/oss_io.py
    - matmaster/adaptors/calculation/job_service.py
    - matmaster/adaptors/calculation/path_adaptor.py
  modified: []

key-decisions:
  - "evomaster.env imports changed to evomaster.env.bohrium to avoid triggering full evomaster.env.__init__.py load chain"
  - "BaseSession TYPE_CHECKING import removed; session param typed as Any with duck-typing (hasattr) for cross-package compatibility"
  - "User-Agent strings updated from EvoMaster to MatMaster in oss_io and job_service"

patterns-established:
  - "Function-level lazy import pattern: from evomaster.env.bohrium import X inside method body, not at module top"
  - "Duck-typing for session detection: 'Local' not in type(session).__name__ avoids cross-package type imports"

requirements-completed: [CALC-01, CALC-02]

# Metrics
duration: 19min
completed: 2026-04-01
---

# Phase 27 Plan 02: Calculation Adaptor Migration Summary

**Migrated 4 calculation modules (env_config, oss_io, job_service, path_adaptor) from evomaster to matmaster with all evomaster imports converted to function-level lazy imports**

## Performance

- **Duration:** 19 min
- **Started:** 2026-04-01T10:40:52Z
- **Completed:** 2026-04-01T11:00:06Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- Created matmaster/adaptors/calculation/ package (1742 lines total across 4 modules + __init__.py)
- env_config (74 lines) and oss_io (139 lines) migrated with zero evomaster dependency
- job_service (680 lines): 8 public functions including query_job_status, get_job_results, terminate_job, download_job_directory
- path_adaptor (849 lines): full CalculationPathAdaptor class with 4-layer path detection, model alias resolution, OSS upload, executor/storage injection
- All 3 evomaster imports converted to function-level lazy imports from evomaster.env.bohrium (not evomaster.env)
- Package-level import matmaster.adaptors.calculation does not trigger evomaster module loading

## Task Commits

Each task was committed atomically:

1. **Task 1: env_config and oss_io migration** - `828c5977` (feat)
2. **Task 2: job_service and path_adaptor with bohrium lazy imports** - `65ef415e` (feat)

## Files Created/Modified
- `matmaster/adaptors/__init__.py` - Package marker for adaptors namespace
- `matmaster/adaptors/calculation/__init__.py` - Public API surface (15 exports)
- `matmaster/adaptors/calculation/env_config.py` - MCP config path resolution + env detection (SERVICE_ENV)
- `matmaster/adaptors/calculation/oss_io.py` - Aliyun OSS upload/download for calculation MCP tools
- `matmaster/adaptors/calculation/job_service.py` - Bohrium OpenAPI job status/results/download/terminate
- `matmaster/adaptors/calculation/path_adaptor.py` - Path-to-OSS URL adaptor with executor/storage injection

## Decisions Made
- Changed `from evomaster.env import` to `from evomaster.env.bohrium import` to avoid triggering evomaster.env.__init__.py full load chain (per RESEARCH Pitfall 5)
- Removed TYPE_CHECKING import of BaseSession; changed session parameter type from BaseSession to Any with duck-typing for session type detection (consistent with Phase 26 pattern)
- Updated User-Agent strings from EvoMaster to MatMaster (oss_io download, job_service HTTP calls)
- Created __init__.py with full public API in Task 1, using temporary stubs for job_service/path_adaptor that were replaced in Task 2

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Created temporary stubs for __init__.py import resolution**
- **Found during:** Task 1
- **Issue:** Plan specified creating __init__.py with imports from all 4 modules in Task 1, but job_service.py and path_adaptor.py don't exist until Task 2
- **Fix:** Created minimal stub files for job_service.py and path_adaptor.py with proper exports (NotImplementedError bodies), replaced with full implementations in Task 2
- **Files modified:** matmaster/adaptors/calculation/job_service.py, matmaster/adaptors/calculation/path_adaptor.py
- **Verification:** Package-level import succeeds after both tasks
- **Committed in:** 828c5977 (Task 1)

**2. [Rule 2 - Missing Critical] Updated User-Agent strings**
- **Found during:** Task 2
- **Issue:** oss_io.py download used 'EvoMaster-Calculation/1.0' and job_service.py used 'EvoMaster-JobService/1.0' - should reflect matmaster identity
- **Fix:** Changed to 'MatMaster-Calculation/1.0' and 'MatMaster-JobService/1.0'
- **Files modified:** matmaster/adaptors/calculation/oss_io.py, matmaster/adaptors/calculation/job_service.py
- **Verification:** Strings verified in output files
- **Committed in:** 828c5977, 65ef415e

---

**Total deviations:** 2 auto-fixed (1 blocking, 1 missing critical)
**Impact on plan:** Both fixes necessary for correctness. No scope creep.

## Issues Encountered
None

## Known Stubs
None - all modules are fully implemented with complete business logic.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- matmaster/adaptors/calculation/ package ready for Plan 03 (lazy_mcp rewiring)
- Callers in matmaster/tools/lazy_mcp.py and tools/builtin/monitor_job can now import from matmaster.adaptors.calculation instead of evomaster.adaptors.calculation
- executor/storage/OSS protocol fully preserved for Bohrium compatibility (CALC-02)

## Self-Check: PASSED

All 6 created files verified present. Both commit hashes (828c5977, 65ef415e) verified in git log.

---
*Phase: 27-mcp-calculation*
*Completed: 2026-04-01*
