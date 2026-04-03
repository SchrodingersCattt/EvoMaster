---
phase: 33-toolrunner-toolscheduler
plan: 04
subsystem: tools
tags: [tool-runtime, effect-level, capability-policy, fast-path, gap-closure]

requires:
  - phase: 33-01
    provides: CapabilityPolicy protocol + DefaultCapabilityPolicy effect_level checks
  - phase: 33-03
    provides: BUILTIN_META injection in ToolCatalog + FullToolRunner fast-path logic
provides:
  - Canonical effect_level enum aligned across BUILTIN_META, ToolRunner fast path, and CapabilityPolicy
  - Regression tests that assert real builtin metadata uses external_write for web-facing tools
  - Regression tests that lock BUILTIN_META to pure_read/local_mutation/external_write only
affects: [phase-33-verification, phase-34-tool-compiler, phase-34-exp-integration]

tech-stack:
  added: []
  patterns:
    - "Canonical effect-level enum: pure_read / local_mutation / external_write"
    - "Policy regression tests built from real BUILTIN_META values to avoid doc/code drift"

key-files:
  created: []
  modified:
    - matmaster/tools/tool_catalog.py
    - matmaster/core/tool_runner.py
    - tests/matmaster/core/test_full_tool_runner.py
    - tests/matmaster/core/test_builtin_claims.py
    - tests/matmaster/core/test_capability_policy.py

key-decisions:
  - "Keep ToolSpec and CapabilityPolicy on external_write as the source-of-truth enum; align BUILTIN_META and fast-path checks to that canonical vocabulary"
  - "Add regression coverage against real BUILTIN_META values instead of only synthetic ToolSpec fixtures"
  - "No code change was needed in matmaster/types/tool_spec.py or matmaster/core/capability_policy.py because both already matched the intended enum contract"

patterns-established:
  - "Fast path semantics now key off pure_read rather than legacy none"
  - "Gap-closure tests should assert both canonical string values and behavior driven by real catalog metadata"

requirements-completed: [TCON-03]

duration: 7min
completed: 2026-04-02
---

# Phase 33 Plan 04: effect_level Canonicalization Summary

**Canonical effect_level semantics now line up across ToolCatalog metadata, FullToolRunner fast path, and CapabilityPolicy, with regression tests anchored to real builtin metadata**

## Performance

- **Duration:** 7 min
- **Started:** 2026-04-02T12:48:01Z
- **Completed:** 2026-04-02T12:55:05Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- BUILTIN_META now consistently uses `pure_read` for read-only builtins and `external_write` for web-facing builtins, matching the ToolSpec enum contract
- FullToolRunner fast-path eligibility now keys off `pure_read`, keeping fast-path semantics aligned with the canonical effect-level vocabulary
- Added regression tests that verify real builtin metadata values and ensure every builtin effect_level stays inside the canonical three-value enum

## Task Commits

Each task was committed atomically:

1. **Task 1: 统一 effect_level 值到 pure_read / local_mutation / external_write 三值枚举** - `e4aed783` (fix)
2. **Task 2: 补充集成测试验证真实内建工具的 effect_level 行为** - `80f34024` (test)

## Files Created/Modified
- `matmaster/tools/tool_catalog.py` - Unified builtin effect_level metadata to the canonical enum
- `matmaster/core/tool_runner.py` - Updated fast-path detection to use `pure_read`
- `tests/matmaster/core/test_full_tool_runner.py` - Synced fast-path tests with canonical enum semantics
- `tests/matmaster/core/test_builtin_claims.py` - Added canonical effect_level coverage for all builtin metadata entries
- `tests/matmaster/core/test_capability_policy.py` - Added real BUILTIN_META-based policy regression tests

## Decisions Made
- Chose the ToolSpec-documented enum (`pure_read` / `local_mutation` / `external_write`) as the single vocabulary, instead of renaming policy checks to match legacy metadata strings
- Kept the new policy regression at the `DefaultCapabilityPolicy.evaluate()` level because `FullToolRunner` with inactive external plane still correctly short-circuits in Layer A; the regression here specifically locks the Layer C string contract
- Treated the pre-existing implementation commit `e4aed783` as valid plan work and resumed from that state by adding the missing regression coverage and summary artifacts

## Deviations from Plan

None - plan behavior now matches the intended canonical enum contract, and the remaining work was to complete the missing regression coverage/documentation around an already-landed implementation fix.

## Issues Encountered

- The implementation fix for Task 1 was already present on the branch before this summary was created, so execution resumed by verifying that commit and adding the missing test coverage rather than redoing the code change.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 33 verification can now rely on a consistent `effect_level` vocabulary across metadata, policy, and fast path
- Plan 33-05 can build `ToolCompiler` on top of canonical builtin metadata without carrying forward legacy `none` / `external_effect` values

## Self-Check: PASSED

- `matmaster/tools/tool_catalog.py` contains no `external_effect` entries and no legacy builtin `\"none\"` effect level values
- `matmaster/core/tool_runner.py` fast-path check uses `effect_level == "pure_read"`
- `uv run pytest tests/matmaster/core/test_capability_policy.py tests/matmaster/core/test_full_tool_runner.py tests/matmaster/core/test_builtin_claims.py tests/matmaster/core/test_structural_validation.py tests/matmaster/core/test_tool_scheduler.py tests/matmaster/core/test_tool_runner.py -x -q` passed (`76 passed`)
- Both task commits (`e4aed783`, `80f34024`) exist in git history

---
*Phase: 33-toolrunner-toolscheduler*
*Completed: 2026-04-02*
