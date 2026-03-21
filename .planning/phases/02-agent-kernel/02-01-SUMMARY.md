---
phase: 02-agent-kernel
plan: 01
subsystem: kernel
tags: [pydantic, enum, protocol, hook, guard, pipeline, tdd, message-types]

# Dependency graph
requires:
  - phase: 01-foundation-contracts
    plan: 01
    provides: "Guard Protocol with GuardContext, GuardResult, RecentCall dataclasses"
  - phase: 01-foundation-contracts
    plan: 02
    provides: "MessageBus thread-safe synchronous event queue"
provides:
  - "Message hierarchy (System/User/Assistant/Tool) with OpenAI-compatible to_api_dict()"
  - "ToolCallData, LLMResponse, StreamChunk Pydantic models"
  - "@runtime_checkable LLMProvider Protocol (chat + chat_stream)"
  - "Hook Protocol with 5 hook points + BaseHook defaults + HookAction enum"
  - "run_* helper functions with short-circuit semantics for intercepting hooks"
  - "EventEmitterHook bridging hooks to MessageBus events"
  - "GuardPipeline with built-in LoopDetectionGuard (not removable) + external guard chaining"
affects: [02-agent-kernel, 03-exp-assembly]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pydantic BaseModel for message types with to_api_dict() serialization"
    - "str-Enum pattern (Role) for role values compatible with API string format"
    - "Protocol + BaseHook pattern: Protocol for interface, concrete class for defaults"
    - "Short-circuit vs observation hook semantics via run_* helper functions"
    - "Fingerprint-based loop detection with sliding window deque"

key-files:
  created:
    - matmaster/engine/__init__.py
    - matmaster/engine/types.py
    - matmaster/types/llm_provider.py
    - matmaster/engine/hooks.py
    - matmaster/engine/guard_pipeline.py
    - tests/matmaster/engine/__init__.py
    - tests/matmaster/engine/conftest.py
    - tests/matmaster/engine/test_types.py
    - tests/matmaster/engine/test_llm_provider.py
    - tests/matmaster/engine/test_hooks.py
    - tests/matmaster/engine/test_guard_pipeline.py
  modified: []

key-decisions:
  - "ToolCallData.arguments is dict[str, Any] (not raw JSON string) -- parsing at provider boundary"
  - "AssistantMessage.to_api_dict() json.dumps tool_call arguments to match OpenAI format"
  - "Hook Protocol separates intercepting hooks (short-circuit) from observation hooks (all-execute)"
  - "EventEmitterHook returns CONTINUE after emitting ToolCallEvent (observation, not interception)"
  - "LoopDetectionGuard uses json.dumps(sort_keys=True) fingerprint with fallback to str() for non-serializable args"
  - "GuardPipeline records calls only after all guards pass (denied calls not tracked)"

patterns-established:
  - "Message hierarchy: BaseModel subclass with Role default + to_api_dict() override"
  - "Hook system: Protocol interface + BaseHook concrete defaults + run_* dispatch helpers"
  - "Guard pipeline: built-in guard first (not removable), external guards appended, first-deny-wins"
  - "Test fixtures: conftest.py with make_tool_call factory + MockLLMProvider for kernel tests"

requirements-completed: [KERN-02, KERN-03, KERN-04, LLMP-01]

# Metrics
duration: 5min
completed: 2026-03-21
---

# Phase 2 Plan 01: Kernel Foundation Modules Summary

**Message hierarchy with OpenAI-compatible serialization, @runtime_checkable LLMProvider Protocol, Hook system with short-circuit semantics + EventEmitterHook, and GuardPipeline with built-in LoopDetectionGuard**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-21T16:31:15Z
- **Completed:** 2026-03-21T16:36:19Z
- **Tasks:** 3
- **Files modified:** 11

## Accomplishments
- Message hierarchy (SystemMessage, UserMessage, AssistantMessage, ToolMessage) with Role enum and to_api_dict() producing OpenAI-compatible dict format -- AssistantMessage handles tool_calls serialization with json.dumps
- LLMProvider @runtime_checkable Protocol defining chat() and chat_stream() interface for any LLM backend
- Hook system with 5 hook points (pre_tool_call, post_tool_call, pre_llm_call, should_continue, on_stream_chunk), BaseHook defaults, HookAction enum (CONTINUE/SKIP), and run_* helpers implementing short-circuit for intercepting hooks and full-execute for observation hooks
- EventEmitterHook bridging kernel events to MessageBus via ToolCallEvent, ToolResultEvent, ThoughtEvent
- GuardPipeline with built-in LoopDetectionGuard (fingerprint-based repeat detection, sliding window deque, not removable), external guard chaining, first-deny-wins evaluation
- 62 unit tests across 4 test files covering all types, protocol conformance, hook semantics, guard pipeline behavior

## Task Commits

Each task was committed atomically (TDD: RED then GREEN):

1. **Task 1: Message types + LLMProvider Protocol** - `f23c78a` (test, RED) + `2582a46` (feat, GREEN)
2. **Task 2: Hook Protocol + BaseHook + HookAction** - `f1c76a8` (test, RED) + `115205e` (feat, GREEN)
3. **Task 3: GuardPipeline + LoopDetectionGuard** - `8009f05` (test, RED) + `4b056e2` (feat, GREEN)

_Note: TDD tasks have separate test (RED) and implementation (GREEN) commits._

## Files Created/Modified
- `matmaster/engine/__init__.py` - Package marker with module docstring
- `matmaster/engine/types.py` - Role enum, ToolCallData, Message hierarchy, LLMResponse, StreamChunk
- `matmaster/types/llm_provider.py` - @runtime_checkable LLMProvider Protocol
- `matmaster/engine/hooks.py` - HookAction enum, Hook Protocol, BaseHook, run_* helpers, EventEmitterHook
- `matmaster/engine/guard_pipeline.py` - LoopDetectionGuard, GuardPipeline with sliding window deque
- `tests/matmaster/engine/__init__.py` - Test package marker
- `tests/matmaster/engine/conftest.py` - mock_tool_call fixture, make_tool_call factory, MockLLMProvider, build_mock_spec
- `tests/matmaster/engine/test_types.py` - 28 tests for message types and serialization
- `tests/matmaster/engine/test_llm_provider.py` - 5 tests for Protocol conformance and usage
- `tests/matmaster/engine/test_hooks.py` - 19 tests for hooks, short-circuit, EventEmitterHook
- `tests/matmaster/engine/test_guard_pipeline.py` - 15 tests for guard pipeline and loop detection

## Decisions Made
- ToolCallData.arguments stored as dict[str, Any] (not raw JSON string) -- parsing happens at the LLM provider boundary, kernel works with structured data
- AssistantMessage.to_api_dict() uses json.dumps on tool_call arguments to produce the JSON string format OpenAI expects
- Hook system uses separate Protocol (interface contract) and BaseHook (concrete defaults) rather than an abstract base class -- follows Phase 1 Protocol pattern
- Intercepting hooks (pre_tool_call, should_continue) short-circuit on first non-default return; observation hooks (post_tool_call, pre_llm_call, on_stream_chunk) always execute all hooks
- EventEmitterHook.pre_tool_call returns CONTINUE after emitting -- it observes but does not intercept
- LoopDetectionGuard fingerprint uses json.dumps(sort_keys=True) for deterministic comparison with TypeError/ValueError fallback to str()
- GuardPipeline only records calls after all guards pass -- denied calls are not tracked in recent_calls

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All kernel foundation modules ready for Plan 02-02 (AgentKernel execution loop)
- types.py provides Message hierarchy consumed by kernel loop for conversation history
- llm_provider.py provides LLMProvider Protocol that Plan 02-02 will use for LLM calls
- hooks.py provides run_* helpers that kernel loop will call at each hook point
- guard_pipeline.py provides GuardPipeline that kernel loop evaluates before each tool call
- conftest.py provides MockLLMProvider and fixtures for kernel integration tests
- No blockers for downstream work

## Self-Check: PASSED

All 11 created files verified on disk. All 6 commit hashes (f23c78a, 2582a46, f1c76a8, 115205e, 8009f05, 4b056e2) verified in git log.

---
*Phase: 02-agent-kernel*
*Completed: 2026-03-21*
