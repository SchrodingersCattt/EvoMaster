---
phase: 30-decoupling-audit
plan: 01
subsystem: testing
tags: [import-audit, isolation, pytest, ast, evomaster-decoupling]

requires:
  - phase: 29-decoupling-audit
    provides: workspace_resolver and legacy code deletion
provides:
  - AST-based import audit covering evomaster/playground/src prefixes with allowlist tracking
  - Isolation test script (scripts/test_matmaster_isolation.sh)
  - tests/matmaster/ free of bare evomaster imports (4 replaced, 2 conditionalized)
  - tests/matmaster/ src imports conditionalized with pytest.importorskip (10 files)
  - LocalSession stop_event support (was no-op, now polls and kills subprocess)
affects: [30-02, 30-03, decoupling-audit, evomaster-deletion]

tech-stack:
  added: []
  patterns:
    - "pytest.importorskip for conditional src/evomaster imports in integration tests"
    - "AST-based import scanning with allowlist for tracking decoupling progress"
    - "Popen-based stop_event polling in LocalSession.exec_bash"

key-files:
  created:
    - tests/matmaster/test_import_audit.py
    - scripts/test_matmaster_isolation.sh
  modified:
    - matmaster/sessions/local.py
    - tests/matmaster/tools/test_bash_tool.py
    - tests/matmaster/tools/test_skill_meta_extras.py
    - tests/matmaster/tools/test_skill_tool_callback.py
    - tests/matmaster/core/test_local_session_stop.py
    - tests/matmaster/config/test_config_consolidation.py
    - tests/matmaster/integration/test_bohrium_execution_contract.py
    - tests/matmaster/integration/test_events_to_messages.py
    - tests/matmaster/integration/test_subagent_event_routing.py
    - tests/matmaster/integration/test_agent_run_service_workspace_upload.py
    - tests/matmaster/integration/test_quota_pipeline.py
    - tests/matmaster/integration/test_e2e_mat_master.py
    - tests/matmaster/integration/test_upstream_scenarios.py
    - tests/matmaster/types/test_worker_registry.py

key-decisions:
  - "SSHSession not yet in matmaster -- used importorskip for evomaster.agent.session.ssh in bohrium test"
  - "Implemented stop_event polling in matmaster LocalSession.exec_bash (was no-op, blocking test)"
  - "SkillTool test rewritten to match matmaster async Tool Protocol interface"
  - "Import audit uses allowlist pattern (24 known violations) instead of xfail"
  - "test_evomaster_tool_adapter.py left with evomaster import -- tests evomaster adapter intentionally"

patterns-established:
  - "Import audit allowlist: known violations tracked as frozenset, new violations = regression, stale entries = resolved"
  - "Isolation test: mv evomaster _evomaster_hidden + trap cleanup EXIT for atomic restore"

requirements_completed: [QUAL-06]

duration: 14min
completed: 2026-04-02
---

# Phase 30 Plan 01: Isolation Test Infrastructure Summary

**AST-based import audit (24 known violations tracked) + 14 test files de-coupled from evomaster/src + isolation test script**

## Performance

- **Duration:** 14 min
- **Started:** 2026-04-01T17:21:09Z
- **Completed:** 2026-04-01T17:35:00Z
- **Tasks:** 2
- **Files modified:** 16

## Accomplishments
- 4 evomaster imports in tests/matmaster/ replaced with matmaster equivalents (LocalSession, Skill, SkillMetaInfo, SkillTool, SkillRegistry)
- 10 files with src imports conditionalized using pytest.importorskip for graceful skip under isolation
- TestPhase30FullIsolation class with AST scanning tracks 24 known matmaster violations (decreasing count gate)
- scripts/test_matmaster_isolation.sh provides atomic hide-and-restore proof of matmaster independence
- LocalSession stop_event support implemented (Popen-based polling, was no-op)
- All 1088 tests pass (3 skipped, 0 failures)

## Task Commits

Each task was committed atomically:

1. **Task 1: Fix evomaster/src imports in 13 test files** - `acd9804e` (fix)
2. **Task 2: Import audit + isolation script** - `ffc5f656` (feat)

## Files Created/Modified

- `tests/matmaster/test_import_audit.py` - AST-based import scanner, Phase 30 audit class with 24-violation allowlist
- `scripts/test_matmaster_isolation.sh` - Hides evomaster/src, runs full tests/matmaster/ suite, atomic restore
- `matmaster/sessions/local.py` - Added stop_event polling support in exec_bash (Popen path)
- `tests/matmaster/tools/test_bash_tool.py` - evomaster LocalSession -> matmaster LocalSession
- `tests/matmaster/tools/test_skill_meta_extras.py` - evomaster Skill/SkillMetaInfo -> matmaster
- `tests/matmaster/tools/test_skill_tool_callback.py` - Rewritten for matmaster SkillTool async Protocol
- `tests/matmaster/core/test_local_session_stop.py` - evomaster LocalSession(Config) -> matmaster LocalSession(path)
- `tests/matmaster/config/test_config_consolidation.py` - EvoMasterConfig import -> importorskip
- `tests/matmaster/integration/test_bohrium_execution_contract.py` - src imports -> importorskip, skills_root path updated
- `tests/matmaster/integration/test_events_to_messages.py` - ChatHistoryConverter -> importorskip
- `tests/matmaster/integration/test_subagent_event_routing.py` - _is_matmaster_source + normalize_event_source -> importorskip
- `tests/matmaster/integration/test_agent_run_service_workspace_upload.py` - _build_workspace_upload_fn -> importorskip
- `tests/matmaster/integration/test_quota_pipeline.py` - AgentRunService -> importorskip
- `tests/matmaster/integration/test_e2e_mat_master.py` - AgentRunService + BohriumSetupResult -> importorskip
- `tests/matmaster/integration/test_upstream_scenarios.py` - AgentRunService + _poll_reply_queue -> importorskip
- `tests/matmaster/types/test_worker_registry.py` - WorkerRegistryServiceAdapter -> importorskip

## Decisions Made

- **SSHSession import conditionalized, not replaced:** matmaster/sessions/ssh.py doesn't exist yet. Used pytest.importorskip for evomaster.agent.session.ssh instead of plan's suggested matmaster path.
- **LocalSession stop_event implemented:** Plan only changed the import, but matmaster's LocalSession ignored stop_event (no-op). Implemented Popen-based polling to match evomaster behavior (Rule 1: bug fix).
- **SkillTool test interface adapted:** matmaster SkillTool uses async execute(dict) vs evomaster's sync execute(session, json_str). Test rewritten to match matmaster Protocol (Rule 1: interface mismatch).
- **Import audit allowlist pattern:** Instead of xfail, used a frozenset of known violation keys. New violations = regression failure. Stale entries = must be cleaned. Tracks progress explicitly.
- **test_evomaster_tool_adapter.py left untouched:** Not in plan scope. Tests evomaster adapter bridge, intentionally imports evomaster.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] LocalSession.exec_bash stop_event was no-op**
- **Found during:** Task 1 (test_local_session_stop.py import replacement)
- **Issue:** matmaster LocalSession accepted stop_event parameter but ignored it (subprocess.run doesn't poll). Test expected exit_code 130 on cancellation.
- **Fix:** Added _exec_bash_with_stop() using subprocess.Popen with poll_interval=0.1s, kills process and returns exit_code 130 when stop_event is set.
- **Files modified:** matmaster/sessions/local.py
- **Verification:** test_local_session_stop.py passes (exit_code 130, cancelled under 5s)
- **Committed in:** acd9804e (Task 1 commit)

**2. [Rule 1 - Bug] SkillTool interface mismatch between evomaster and matmaster**
- **Found during:** Task 1 (test_skill_tool_callback.py import replacement)
- **Issue:** evomaster SkillTool(registry, on_skill_hit) with sync execute(session, json_str) vs matmaster SkillTool(registry, session, on_skill_hit) with async execute(dict). Test would TypeError.
- **Fix:** Rewrote test to use matmaster SkillTool constructor and asyncio.run(tool.execute({...}))
- **Files modified:** tests/matmaster/tools/test_skill_tool_callback.py
- **Verification:** All 3 callback tests pass
- **Committed in:** acd9804e (Task 1 commit)

**3. [Rule 3 - Blocking] test_import_audit.py did not exist**
- **Found during:** Task 2 (plan referenced existing file)
- **Issue:** Plan assumed test_import_audit.py with helper functions already existed. File was missing.
- **Fix:** Created complete file with _find_matmaster_py_files(), _find_all_imports_matching(), _is_inside_type_checking() utilities and TestPhase30FullIsolation class.
- **Files modified:** tests/matmaster/test_import_audit.py (created)
- **Verification:** 2 tests pass, audit detects all 24 known violations
- **Committed in:** ffc5f656 (Task 2 commit)

---

**Total deviations:** 3 auto-fixed (2 Rule 1 bugs, 1 Rule 3 blocking)
**Impact on plan:** All necessary for test correctness. No scope creep.

## Issues Encountered
None beyond the deviations documented above.

## Known Stubs
None - all artifacts are fully functional.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Import audit infrastructure ready for Plan 02 isolation test runs
- Isolation script ready to prove matmaster independence when evomaster/src are hidden
- 24 known violations in matmaster/ tracked; each resolved violation should be removed from KNOWN_VIOLATIONS frozenset
- test_evomaster_tool_adapter.py has one remaining evomaster import (out of scope, tests adapter bridge)

---
*Phase: 30-decoupling-audit*
*Completed: 2026-04-02*
