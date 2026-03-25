---
phase: 02-agent-kernel
plan: 02
subsystem: kernel
tags: [agent-kernel, execution-loop, openai, llm-provider, streaming, tool-calls]

# Dependency graph
requires:
  - phase: 02-agent-kernel/01
    provides: "Hook Protocol, BaseHook, GuardPipeline, LLMProvider Protocol, Message types, StreamChunk"
  - phase: 01-foundation-contracts
    provides: "AgentRuntimeSpec, FinishEvent, Guard Protocol, GuardResult, MessageBus"
provides:
  - "AgentKernel.run(spec, task, stop_event) -- full execution loop with 4 termination paths"
  - "OpenAIProvider -- concrete LLMProvider implementation using openai SDK"
  - "AgentRuntimeSpec.llm_provider typed as LLMProvider, hooks typed as list[Hook]"
  - "matmaster.engine public API exports via __init__.py"
affects: [03-exp-layer, 04-playground-layer]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "TYPE_CHECKING guard for cross-package circular imports (kernel.py -> contracts.runtime)"
    - "Streaming chunk accumulation with tool_call delta reassembly by index"
    - "Guard -> Hook -> Tool evaluation chain with short-circuit semantics"

key-files:
  created:
    - matmaster/engine/agent.py
    - matmaster/providers/openai_provider.py
    - tests/matmaster/engine/test_kernel.py
    - tests/matmaster/engine/test_openai_provider.py
  modified:
    - matmaster/types/runtime.py
    - matmaster/engine/__init__.py
    - tests/matmaster/types/test_runtime.py

key-decisions:
  - "TYPE_CHECKING guard in kernel.py to break circular import with contracts.runtime"
  - "OpenAI SDK retry logic delegated to client-level max_retries, kernel does not retry"
  - "ToolCallData.arguments parsed at provider boundary (dict, not raw JSON string)"

patterns-established:
  - "Guard evaluation before hooks: blocked calls skip hooks entirely"
  - "Hook SKIP bypasses tool execution but still sends ToolMessage to LLM"
  - "All termination paths through unified _finish() method"
  - "Streaming-first: kernel uses chat_stream() by default, accumulates to LLMResponse"

requirements-completed: [KERN-01, KERN-04, LLMP-01]

# Metrics
duration: 7min
completed: 2026-03-21
---

# Phase 2 Plan 2: Kernel Execution Loop & OpenAIProvider Summary

**AgentKernel execution loop with 4 termination paths (natural/max_turns/cancelled/hook_stopped), streaming tool_call delta reassembly, guard->hook->tool chain, and OpenAIProvider as concrete LLMProvider**

## Performance

- **Duration:** 7 min
- **Started:** 2026-03-21T16:40:13Z
- **Completed:** 2026-03-21T16:47:58Z
- **Tasks:** 3
- **Files modified:** 7

## Accomplishments
- AgentKernel.run() executes complete LLM -> guard -> hook -> tool -> loop cycle with streaming chunk accumulation and tool_call delta reassembly
- OpenAIProvider satisfies LLMProvider Protocol with real OpenAI SDK (chat + chat_stream), all tests using mocked client
- AgentRuntimeSpec typed with concrete Protocol types (LLMProvider, Hook) instead of Any
- matmaster.engine public API exports all 18 types via __init__.py

## Task Commits

Each task was committed atomically:

1. **Task 1: AgentKernel execution loop** - `0cd6e0a` (feat) -- TDD: 12 tests
2. **Task 2: OpenAIProvider** - `2e1399d` (feat) -- TDD: 18 tests
3. **Task 3: Update AgentRuntimeSpec types + kernel exports** - `d9a066b` (feat)

## Files Created/Modified
- `matmaster/engine/agent.py` - AgentKernel class with run(), _call_llm(), _parse_arguments(), _finish()
- `matmaster/providers/openai_provider.py` - OpenAIProvider class wrapping openai.OpenAI client
- `matmaster/engine/__init__.py` - Public API re-exports for all 18 kernel types
- `matmaster/types/runtime.py` - AgentRuntimeSpec with typed llm_provider and hooks fields
- `tests/matmaster/engine/test_kernel.py` - 12 tests covering all termination paths and execution flows
- `tests/matmaster/engine/test_openai_provider.py` - 18 tests with mocked OpenAI client
- `tests/matmaster/types/test_runtime.py` - Updated tests for typed fields + 3 new Protocol tests

## Decisions Made
- Used TYPE_CHECKING guard in kernel.py to break circular import between kernel and contracts packages (contracts.runtime imports kernel.hooks, kernel.kernel imports contracts.runtime)
- OpenAI SDK retry is client-level (max_retries parameter), kernel does not implement retry
- Invalid JSON in tool_call arguments falls back to {"_raw": raw_string} gracefully

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed circular import between kernel and contracts packages**
- **Found during:** Task 3 (Update AgentRuntimeSpec types)
- **Issue:** Adding `from matmaster.engine.hooks import Hook` to contracts/runtime.py created circular import: contracts.runtime -> kernel.__init__ -> kernel.kernel -> contracts.runtime
- **Fix:** Used TYPE_CHECKING guard in kernel.py for the AgentRuntimeSpec import, which is only needed for type annotations (deferred by `from __future__ import annotations`)
- **Files modified:** matmaster/engine/agent.py
- **Verification:** All 177 matmaster tests pass
- **Committed in:** d9a066b (Task 3 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Necessary fix for cross-package type imports. Standard Python pattern for circular imports with Protocol types. No scope creep.

## Issues Encountered
None beyond the circular import documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- AgentKernel is ready for consumption by Phase 3 (Exp layer) which will define ToolRegistry and assemble AgentRuntimeSpec
- All kernel types exported via matmaster.engine for downstream imports
- OpenAIProvider ready for real API usage when api_key is configured

## Self-Check: PASSED

All 7 created/modified files verified on disk. All 3 task commits (0cd6e0a, 2e1399d, d9a066b) verified in git log.

---
*Phase: 02-agent-kernel*
*Completed: 2026-03-21*
