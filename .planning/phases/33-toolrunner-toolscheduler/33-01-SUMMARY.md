---
phase: 33-toolrunner-toolscheduler
plan: 01
subsystem: tools
tags: [jsonschema, protocol, pydantic, frozen-model, constraint-validation]

requires:
  - phase: 32-kernel-generator-tool-runtime-v2
    provides: "ToolSpec, ToolBinding, ToolInstance, ToolDecision, RuntimeTopology, SessionCapabilities, ToolPlane type system"
provides:
  - "StructuralValidation (Layer A): stateless args_schema + plane + capability validation"
  - "CapabilityPolicy Protocol + DefaultCapabilityPolicy (Layer C): effect_level + capability matching"
affects: [33-02-toolrunner, 33-03-toolscheduler, 34-exp-integration]

tech-stack:
  added: [jsonschema]
  patterns: [three-layer-constraint-model, protocol-based-policy, deny-with-guidance]

key-files:
  created:
    - matmaster/core/structural_validation.py
    - matmaster/core/capability_policy.py
    - tests/matmaster/core/test_structural_validation.py
    - tests/matmaster/core/test_capability_policy.py
  modified: []

key-decisions:
  - "Used actual codebase effect_level values (pure_read/local_mutation/external_write) instead of plan interface snippet values (none/local_mutation/external_effect)"
  - "StructuralValidation checks plane-level capability (shell.execute on SESSION_SHELL), CapabilityPolicy checks fine-grained capability matching -- complementary not overlapping"

patterns-established:
  - "Three-layer constraint model: Layer A (StructuralValidation) -> Layer B (RunStateGuard, future) -> Layer C (CapabilityPolicy)"
  - "Deny-with-guidance pattern: ToolDecision(decision='deny', guidance='...') for policy violations, bare deny for structural failures"
  - "CapabilityPolicy as @runtime_checkable Protocol: custom policies can be swapped in without inheritance"

requirements-completed: [TCON-01, TCON-03]

duration: 5min
completed: 2026-04-02
---

# Phase 33 Plan 01: StructuralValidation + CapabilityPolicy Summary

**Layer A stateless args/topology validation (jsonschema) + Layer C effect_level/capability policy (@runtime_checkable Protocol) with 22 passing tests**

## Performance

- **Duration:** 5 min
- **Started:** 2026-04-02T11:39:52Z
- **Completed:** 2026-04-02T11:44:45Z
- **Tasks:** 2 (both TDD: RED -> GREEN)
- **Files created:** 4

## Accomplishments

- StructuralValidation with 3-step validate(): args_schema (jsonschema.validate), plane activation, session capabilities
- CapabilityPolicy Protocol (@runtime_checkable) + DefaultCapabilityPolicy with effect_level and capability matching
- 22 tests covering all deny/allow scenarios across both constraint layers
- Deny-with-guidance pattern established for policy violations (CapabilityPolicy returns guidance for LLM prompt injection)

## Task Commits

Each task was committed atomically (TDD: test then implementation):

1. **Task 1: StructuralValidation (RED)** - `b9b71fde` (test)
2. **Task 1: StructuralValidation (GREEN)** - `4f4b0458` (feat)
3. **Task 2: CapabilityPolicy (RED)** - `6e913069` (test)
4. **Task 2: CapabilityPolicy (GREEN)** - `7617b750` (feat)

## Files Created/Modified

- `matmaster/core/structural_validation.py` - Layer A: 3-step stateless validation (args_schema, plane, capabilities)
- `matmaster/core/capability_policy.py` - Layer C: @runtime_checkable Protocol + DefaultCapabilityPolicy
- `tests/matmaster/core/test_structural_validation.py` - 11 tests: TestArgsSchema(4) + TestPlaneCheck(3) + TestCapabilities(4)
- `tests/matmaster/core/test_capability_policy.py` - 11 tests: TestEffectLevel(4) + TestCapabilityMatch(5) + TestProtocol(2)

## Decisions Made

1. **Used actual codebase effect_level values** -- Plan interface snippet referenced `"none"` and `"external_effect"` but actual ToolSpec uses `"pure_read"` and `"external_write"`. Used real codebase values for correctness.
2. **Complementary capability checks** -- StructuralValidation checks topology-level enablement (is the plane active, does session support shell.execute on SESSION_SHELL). CapabilityPolicy checks fine-grained policy (artifact.download requires upload_support, shell.execute requires shell_input, effect_level requires EXTERNAL_SERVICE). Not overlapping -- they check at different granularity levels.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Restored Phase 32 type files in worktree**
- **Found during:** Task 1 (StructuralValidation GREEN phase)
- **Issue:** tool_decision.py, tool_spec.py, topology.py existed in branch history (commit 82a04ceb from Phase 32 Plan 01) but not in this worktree
- **Fix:** Restored files from commit 82a04ceb using `git show`
- **Files added:** matmaster/types/tool_decision.py, tool_spec.py, topology.py
- **Verification:** Import succeeds, all tests pass
- **Committed in:** 4f4b0458 (part of Task 1 GREEN commit)

**2. [Rule 3 - Blocking] effect_level value mismatch between plan and codebase**
- **Found during:** Task 2 (CapabilityPolicy implementation)
- **Issue:** Plan references `effect_level="external_effect"` and `"none"`, but actual ToolSpec default comment shows `"pure_read" | "local_mutation" | "external_write"`
- **Fix:** Used actual codebase values (`external_write`, `pure_read`) instead of plan values
- **Verification:** Tests written and passing with real values

---

**Total deviations:** 2 auto-fixed (2 blocking)
**Impact on plan:** Both fixes necessary for correct execution. No scope creep.

## Issues Encountered

- Worktree was based on older commit without Phase 32 type files -- resolved by restoring from branch history
- pytest not installed in fresh .venv -- resolved by `uv pip install pytest`

## User Setup Required

None - no external service configuration required.

## Known Stubs

None -- both modules are complete implementations with no placeholder data.

## Next Phase Readiness

- StructuralValidation and CapabilityPolicy are ready to be consumed by ToolRunner (Plan 02)
- RunStateGuard (Layer B) is a future plan dependency, not blocking Plan 02
- Both modules have stable method signatures matching RESEARCH.md Pattern 3 and Pattern 4

## Self-Check: PASSED

- All 5 files exist
- All 4 commits found in history
- 22/22 tests pass

---
*Phase: 33-toolrunner-toolscheduler*
*Completed: 2026-04-02*
