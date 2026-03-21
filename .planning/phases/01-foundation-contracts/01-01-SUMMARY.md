---
phase: 01-foundation-contracts
plan: 01
subsystem: contracts
tags: [pydantic, frozen-model, discriminated-union, protocol, dataclass, typing]

# Dependency graph
requires: []
provides:
  - "Guard Protocol with GuardContext, GuardResult, RecentCall dataclasses"
  - "PlaygroundContext frozen Pydantic model (Layer 1 boundary contract)"
  - "AgentRuntimeSpec frozen Pydantic model with CompactionConfig (Layer 2 boundary contract)"
  - "16 event types: 7 AgentEvent + 9 SystemEvent + BusEvent union"
  - "matmaster/contracts/ package with re-exports"
affects: [02-agent-kernel, 03-exp-assembly, 04-playground-layer, 05-integration-quality]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pydantic ConfigDict(frozen=True) for boundary contracts"
    - "typing.Protocol (runtime_checkable) for component interfaces"
    - "Pydantic discriminated union with Literal type field"
    - "dataclass for internal state structures (Guard support types)"

key-files:
  created:
    - matmaster/__init__.py
    - matmaster/contracts/__init__.py
    - matmaster/contracts/guards.py
    - matmaster/contracts/context.py
    - matmaster/contracts/runtime.py
    - matmaster/contracts/events.py
    - tests/matmaster/contracts/test_guards.py
    - tests/matmaster/contracts/test_context.py
    - tests/matmaster/contracts/test_runtime.py
    - tests/matmaster/contracts/test_events.py
  modified: []

key-decisions:
  - "Guard Protocol uses @runtime_checkable for isinstance checks at runtime"
  - "CONT-05 TerminationPolicy simplified to AgentRuntimeSpec.max_turns int field (default 100)"
  - "BusEvent enumerates all 16 types directly (not Union[AgentEvent, SystemEvent]) for Pydantic discriminator compatibility"
  - "Zero new dependencies -- all from Pydantic v2 (existing) and stdlib"

patterns-established:
  - "Pydantic frozen model pattern: ConfigDict(frozen=True) on all boundary contracts"
  - "Event type pattern: Literal['type_name'] default field + Annotated Union with Field(discriminator='type')"
  - "Guard interface pattern: Protocol with evaluate(GuardContext) -> GuardResult"
  - "Package export pattern: matmaster/contracts/__init__.py re-exports all public types"

requirements-completed: [CONT-01, CONT-02, CONT-03, CONT-04, CONT-05]

# Metrics
duration: 6min
completed: 2026-03-21
---

# Phase 1 Plan 01: Foundation Contracts Summary

**Pydantic frozen models for PlaygroundContext/AgentRuntimeSpec boundary contracts, Guard Protocol with dataclass support types, and 16-type BusEvent discriminated union hierarchy**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-21T14:28:22Z
- **Completed:** 2026-03-21T14:35:16Z
- **Tasks:** 3
- **Files modified:** 10

## Accomplishments
- Guard Protocol (runtime_checkable) with GuardContext, GuardResult, RecentCall -- complete guard evaluation interface for kernel tool call gating
- PlaygroundContext frozen model with workdir/session_type/cache_area/env_vars/mcp_manager/skill_registry/run_meta -- Layer 1 boundary contract
- AgentRuntimeSpec frozen model with llm_provider/tool_registry/guards/max_turns/hooks/compaction/system_prompt/mode -- Layer 2 boundary contract (CONT-05: max_turns replaces TerminationPolicy)
- 16 event types in AgentEvent (7) + SystemEvent (9) discriminated unions, unified as BusEvent -- complete event type foundation for MessageBus
- 48 unit tests covering instantiation, frozen enforcement, Protocol conformance, discriminator dispatch, serialization roundtrip, type uniqueness

## Task Commits

Each task was committed atomically:

1. **Task 1: Guard Protocol and PlaygroundContext** - `243fe4f` (feat)
2. **Task 2: AgentRuntimeSpec and CompactionConfig** - `c1ec789` (feat)
3. **Task 3: AgentEvent, SystemEvent, BusEvent unions** - `92edd37` (feat)

## Files Created/Modified
- `matmaster/__init__.py` - Top-level package marker
- `matmaster/contracts/__init__.py` - Re-exports all 27 public types
- `matmaster/contracts/guards.py` - Guard Protocol, GuardContext, GuardResult, RecentCall
- `matmaster/contracts/context.py` - PlaygroundContext frozen model
- `matmaster/contracts/runtime.py` - AgentRuntimeSpec, CompactionConfig frozen models
- `matmaster/contracts/events.py` - All 16 event types + AgentEvent/SystemEvent/BusEvent unions
- `tests/matmaster/contracts/__init__.py` - Test package marker
- `tests/matmaster/contracts/test_guards.py` - 8 tests for Guard types
- `tests/matmaster/contracts/test_context.py` - 5 tests for PlaygroundContext
- `tests/matmaster/contracts/test_runtime.py` - 9 tests for AgentRuntimeSpec
- `tests/matmaster/contracts/test_events.py` - 26 tests for event types and unions

## Decisions Made
- Guard Protocol uses `@runtime_checkable` to enable isinstance checks at runtime, supporting both static (mypy) and dynamic type checking
- CONT-05 TerminationPolicy simplified to `AgentRuntimeSpec.max_turns: int = 100` per CONTEXT.md locked decision -- no separate class
- BusEvent union lists all 16 individual types directly rather than `Union[AgentEvent, SystemEvent]` because Pydantic discriminated union requires all variants at the same level for the discriminator to work correctly
- Zero new dependencies added: all functionality from Pydantic v2 (already in pyproject.toml) and Python stdlib (dataclasses, typing)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- pytest was not installed in the project venv; installed via `python -m pip install pytest` (Rule 3: blocking dependency)

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All contract types ready for consumption by Plan 01-02 (MessageBus/QueueBridge)
- Phase 2 (Agent Kernel) can reference Guard Protocol, AgentRuntimeSpec, and event types
- Phase 3 (Exp Assembly) can reference PlaygroundContext and AgentRuntimeSpec
- No blockers for downstream work

---
*Phase: 01-foundation-contracts*
*Completed: 2026-03-21*
