---
phase: 26-tool
plan: 02
subsystem: tools
tags: [monitor-job, builtin-tool, lazy-import, evomaster-decoupling]

# Dependency graph
requires:
  - phase: 14-builtin-tool
    provides: BuiltinTool ABC base class and Tool Protocol
provides:
  - MonitorJobTool as matmaster native BuiltinTool subclass
  - 7-file monitor_job package with lazy evomaster.adaptors imports
  - Duck-typed session detection (no SSHSession isinstance)
affects: [26-03, 27-calculation, exp-tool-registration]

# Tech tracking
tech-stack:
  added: []
  patterns: [lazy-import for evomaster.adaptors.calculation, getattr duck-typing for session type detection]

key-files:
  created:
    - matmaster/tools/builtin/monitor_job/__init__.py
    - matmaster/tools/builtin/monitor_job/_tool.py
    - matmaster/tools/builtin/monitor_job/_constants.py
    - matmaster/tools/builtin/monitor_job/_lifecycle.py
    - matmaster/tools/builtin/monitor_job/_download.py
    - matmaster/tools/builtin/monitor_job/_llm.py
    - matmaster/tools/builtin/monitor_job/_logs.py
  modified: []

key-decisions:
  - "Lazy import strategy for evomaster.adaptors.calculation: function-level import in each usage site, no global try/except"
  - "Duck-typing via hasattr(session, '_env') + hasattr(env, 'upload_file') replaces isinstance(SSHSession)"
  - "Session type annotations use Any instead of BaseSession to avoid evomaster.agent.session import"

patterns-established:
  - "Lazy import pattern: evomaster adaptor dependencies moved inside function bodies to defer module load"
  - "Duck-type session detection: hasattr chain instead of isinstance for cross-package type checking"

requirements-completed: [TOOL-09]

# Metrics
duration: 6min
completed: 2026-04-01
---

# Phase 26 Plan 02: MonitorJobTool Migration Summary

**MonitorJobTool ported to matmaster BuiltinTool with json_schema interface, lazy evomaster.adaptors imports, and duck-typed session detection**

## Performance

- **Duration:** 6 min
- **Started:** 2026-04-01T08:42:41Z
- **Completed:** 2026-04-01T08:49:20Z
- **Tasks:** 2
- **Files created:** 7

## Accomplishments
- Ported MonitorJobTool from evomaster to matmaster as native BuiltinTool subclass with json_schema + arguments dict interface
- Eliminated all evomaster.agent.tools.builtin and evomaster.agent.session runtime imports from the monitor_job package
- Converted 4 submodules (_lifecycle, _download, _llm, _logs) to use lazy imports for evomaster.adaptors.calculation
- Replaced all isinstance(SSHSession) checks with duck-typed hasattr detection

## Task Commits

Each task was committed atomically:

1. **Task 1: Create MonitorJobTool core files** - `9fdd6254` (feat)
2. **Task 2: Port submodules with lazy imports** - `ec9752d9` (feat)

## Files Created/Modified
- `matmaster/tools/builtin/monitor_job/__init__.py` - Package init, exports MonitorJobTool and run_monitor_decision_once
- `matmaster/tools/builtin/monitor_job/_tool.py` - MonitorJobTool(BuiltinTool) with json_schema and _execute method
- `matmaster/tools/builtin/monitor_job/_constants.py` - Constants (REPO_ROOT parents[4], terminal states, log patterns, LLM prompt)
- `matmaster/tools/builtin/monitor_job/_lifecycle.py` - Core poll loop with lazy job_service imports
- `matmaster/tools/builtin/monitor_job/_download.py` - Result download helpers with lazy job_service imports
- `matmaster/tools/builtin/monitor_job/_llm.py` - LLM decision + job termination with lazy config/utils imports
- `matmaster/tools/builtin/monitor_job/_logs.py` - Log discovery and one-shot monitor with lazy job_service imports

## Decisions Made
- Used function-level lazy imports (not global try/except) for evomaster.adaptors.calculation -- if the adaptor is unavailable, errors surface at call time which is correct (MonitorJobTool cannot function without Bohrium adaptors)
- Duck-typed session with `hasattr(session, '_env') and hasattr(env, 'upload_file')` pattern -- avoids importing SSHSession while preserving the same behavioral check
- Removed MonitorJobParams (Pydantic BaseToolParams) entirely -- json_schema ClassVar replaces it per BuiltinTool contract

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

The pre-existing import chain `matmaster.tools.__init__ -> EvoToolAdapter -> evomaster` causes evomaster modules to appear in sys.modules when importing any matmaster.tools submodule. This is a pre-existing condition (not caused by this plan). The verification confirmed that importing matmaster.tools.builtin.monitor_job adds zero additional evomaster modules beyond what matmaster.tools.builtin.base already loads.

## Known Stubs

None - all functions are fully implemented, no placeholder data.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- MonitorJobTool is ready for registration in ToolRegistry (Plan 03 or exp.py integration)
- evomaster.adaptors.calculation dependency remains as lazy import (target for Phase 27 migration)
- EvoToolAdapter still wraps the old evomaster MonitorJobTool in exp.py -- that registration path can now switch to the matmaster native tool

## Self-Check: PASSED

- All 7 created files exist
- Commit 9fdd6254 (Task 1) found
- Commit ec9752d9 (Task 2) found
- SUMMARY.md exists

---
*Phase: 26-tool*
*Completed: 2026-04-01*
