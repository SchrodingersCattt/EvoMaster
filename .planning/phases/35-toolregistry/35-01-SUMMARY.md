---
phase: 35-toolregistry
plan: 01
subsystem: tools
tags: [guard, capability-policy, read-before-modify, bash-safety, constraint-model]

# Dependency graph
requires:
  - phase: 33
    provides: FullToolRunner seven-step execution chain, ToolScheduler, StructuralValidation
provides:
  - ReadBeforeModifyGuard in GuardPipeline (Layer B)
  - DefaultCapabilityPolicy with bash/python safety checks (Layer C)
  - WriteTool.validate_input() for read-before-modify via input_validator path
  - ToolDecision type for policy return values
  - Tools (WriteTool/EditTool/BashTool) as pure execution layers
affects: [35-02, 35-03]

# Tech tracking
tech-stack:
  added: []
  patterns: [constraint-model-migration, guard-pipeline-injection, input-validator-path]

key-files:
  created:
    - matmaster/core/capability_policy.py
    - matmaster/types/tool_decision.py
    - tests/matmaster/core/test_capability_policy.py
  modified:
    - matmaster/types/guards.py
    - matmaster/core/guard_pipeline.py
    - matmaster/core/exp.py
    - matmaster/core/agent.py
    - matmaster/types/runtime.py
    - matmaster/tools/builtin/write_tool.py
    - matmaster/tools/builtin/edit_tool.py
    - matmaster/tools/builtin/bash_tool.py
    - tests/matmaster/core/test_guard_pipeline.py
    - tests/matmaster/tools/test_write_tool.py
    - tests/matmaster/tools/test_edit_tool.py
    - tests/matmaster/tools/test_bash_tool.py

key-decisions:
  - "write_file excluded from ReadBeforeModifyGuard._MODIFY_TOOLS; uses validate_input instead (needs session.path_exists)"
  - "ReadTracker stored as Exp instance variable for cross-method access between _init_builtin_tools and build_runtime"
  - "AgentRuntimeSpec gains read_tracker field; agent.py passes it to GuardPipeline constructor"

patterns-established:
  - "Constraint model Layer B: ReadBeforeModifyGuard as external guard in GuardPipeline"
  - "Constraint model Layer C: DefaultCapabilityPolicy.check_bash_safety() for tool-specific safety"
  - "input_validator path: WriteTool.validate_input() for session-dependent checks that Guard layer should not handle"

requirements-completed: [CMIG-01, CMIG-02]

# Metrics
duration: 9min
completed: 2026-04-03
---

# Phase 35 Plan 01: Constraint Migration Summary

**Read-before-modify and bash safety checks migrated from tool internals to three-layer constraint model (GuardPipeline + CapabilityPolicy), tools become pure execution layers**

## Performance

- **Duration:** 9 min
- **Started:** 2026-04-03T04:27:19Z
- **Completed:** 2026-04-03T04:36:04Z
- **Tasks:** 2
- **Files modified:** 15

## Accomplishments
- ReadBeforeModifyGuard enforces edit_file read-before-modify via GuardPipeline (Layer B)
- DefaultCapabilityPolicy handles bash dangerous command patterns and python content scanning (Layer C)
- WriteTool/EditTool/BashTool stripped of all internal safety checks, now pure execution layers
- Exp.build_runtime() injects ReadBeforeModifyGuard + ReadTracker into GuardPipeline
- 75 tests pass across all affected modules

## Task Commits

Each task was committed atomically:

1. **Task 1: ReadBeforeModifyGuard + CapabilityPolicy bash safety** - TDD workflow
   - `f13fb73d` (test: failing tests for guard + policy)
   - `021bf28b` (feat: implementation passing all tests)
2. **Task 2: Tool cleanup + Exp.build_runtime() injection** - `00d61473` (feat)

## Files Created/Modified
- `matmaster/types/tool_decision.py` - ToolDecision Pydantic model for policy returns
- `matmaster/core/capability_policy.py` - DefaultCapabilityPolicy with bash/python safety patterns
- `matmaster/types/guards.py` - GuardContext.read_tracker field added
- `matmaster/core/guard_pipeline.py` - ReadBeforeModifyGuard class + GuardPipeline read_tracker param
- `matmaster/types/runtime.py` - AgentRuntimeSpec.read_tracker field
- `matmaster/core/agent.py` - GuardPipeline construction passes read_tracker
- `matmaster/core/exp.py` - Injects ReadBeforeModifyGuard + read_tracker into spec
- `matmaster/tools/builtin/write_tool.py` - _execute() cleaned, validate_input() added
- `matmaster/tools/builtin/edit_tool.py` - _execute() read-before-modify check removed
- `matmaster/tools/builtin/bash_tool.py` - All danger patterns/functions/calls deleted
- `tests/matmaster/core/test_guard_pipeline.py` - 6 new ReadBeforeModifyGuard tests
- `tests/matmaster/core/test_capability_policy.py` - 12 new bash/python safety tests
- `tests/matmaster/tools/test_write_tool.py` - Updated for validate_input path
- `tests/matmaster/tools/test_edit_tool.py` - Updated for guard-layer enforcement
- `tests/matmaster/tools/test_bash_tool.py` - Updated for pure execution layer

## Decisions Made
- write_file excluded from ReadBeforeModifyGuard._MODIFY_TOOLS because its new-file detection requires session.path_exists (a session capability that Guard layer should not depend on). Instead, write_file uses validate_input() for the input_validator path.
- ReadTracker stored as Exp._read_tracker instance variable to enable cross-method access between _init_builtin_tools (creates tracker) and build_runtime (injects into spec).
- AgentRuntimeSpec gains read_tracker field; agent.py passes it through to GuardPipeline constructor.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Created ToolDecision type (not yet in codebase)**
- **Found during:** Task 1 (CapabilityPolicy implementation)
- **Issue:** Plan interfaces referenced matmaster/types/tool_decision.py but it did not exist
- **Fix:** Created ToolDecision Pydantic model with decision/reason/guidance fields
- **Files modified:** matmaster/types/tool_decision.py
- **Verification:** Import succeeds, tests pass
- **Committed in:** f13fb73d (Task 1 test commit)

**2. [Rule 3 - Blocking] Added read_tracker to AgentRuntimeSpec + agent.py**
- **Found during:** Task 2 (Exp.build_runtime injection)
- **Issue:** GuardPipeline needs read_tracker but agent.py constructs it from spec.guards only
- **Fix:** Added read_tracker field to AgentRuntimeSpec, updated agent.py to pass it
- **Files modified:** matmaster/types/runtime.py, matmaster/core/agent.py
- **Verification:** Guard pipeline correctly receives tracker, all tests pass
- **Committed in:** 00d61473 (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (2 blocking)
**Impact on plan:** Both were necessary infrastructure for the planned functionality. No scope creep.

## Issues Encountered
None

## Known Stubs
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Constraint model Layer B (GuardPipeline) and Layer C (CapabilityPolicy) foundations in place
- ToolRegistry deprecation (35-02) can proceed with the constraint layers active
- Future plans can extend DefaultCapabilityPolicy with additional tool-specific checks

---
*Phase: 35-toolregistry*
*Completed: 2026-04-03*
