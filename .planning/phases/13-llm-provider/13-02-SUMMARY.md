---
phase: 13-llm-provider
plan: 02
subsystem: core
tags: [async, asyncio, context-compactor, bridge-loop, agent-kernel, event-loop]

# Dependency graph
requires:
  - phase: 13-llm-provider
    provides: async LLMProvider Protocol with __aenter__/__aexit__ and AsyncOpenAI-based OpenAIProvider
provides:
  - async ContextCompactor (_summarize + compact_if_needed)
  - AgentKernel shared bridge loop for async provider lifecycle and streaming
  - summary_provider separate lifecycle management (HIGH-1 fix)
  - _sync_iterate_async / _sync_call_async bridge utilities (Phase 13-16 transition)
affects: [14-context-compactor, 17-kernel-async, 18-exp-async]

# Tech tracking
tech-stack:
  added: []
  patterns: [sync-async-bridge-loop, provider-lifecycle-in-kernel, async-compactor]

key-files:
  created: []
  modified:
    - matmaster/core/context_compactor.py
    - matmaster/core/agent.py
    - tests/matmaster/core/test_context_compactor.py
    - tests/matmaster/core/test_agent.py
    - tests/matmaster/core/conftest.py
    - tests/matmaster/devshell/test_compaction_via_devshell.py

key-decisions:
  - "ContextCompactor._summarize and compact_if_needed are async, await provider.chat()"
  - "AgentKernel.run() creates ONE shared asyncio.new_event_loop() for all async bridging"
  - "summary_provider lifecycle managed separately when it differs from llm_provider (HIGH-1)"
  - "_call_llm creates temporary bridge loop when called directly (backward compat for tests)"
  - "E2E/Kernel integration tests skipped per D-08, deferred to Phase 17-18"

patterns-established:
  - "sync-async-bridge-loop: single shared event loop bridges all async calls in sync Kernel"
  - "provider-lifecycle-in-kernel: run() manages __aenter__/__aexit__ for llm_provider and summary_provider"
  - "bridge-functions-marked: _sync_iterate_async/_sync_call_async clearly marked Phase 13-16, removable in Phase 17"

requirements-completed: [LLMP-01, LLMP-02, LLMP-03]

# Metrics
duration: 13min
completed: 2026-03-27
---

# Phase 13 Plan 02: ContextCompactor Async + Kernel Bridge Loop Summary

**ContextCompactor async + AgentKernel 单一共享 bridge loop 桥接 async provider lifecycle/streaming/compaction，全部 142 tests 通过**

## Performance

- **Duration:** 13 min
- **Started:** 2026-03-26T18:31:13Z
- **Completed:** 2026-03-26T18:44:18Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- ContextCompactor._summarize and compact_if_needed converted to async def with await provider.chat()
- AgentKernel.run() creates single shared bridge loop, manages both llm_provider AND summary_provider async lifecycle
- All mock providers across 3 test files adapted to async (aenter/aexit/chat/chat_stream)
- 142 passed, 3 skipped (E2E/Kernel integration deferred to Phase 17-18 per D-08)

## Task Commits

Each task was committed atomically:

1. **Task 1: ContextCompactor async + test migration** - `cc3c6cd` (feat)
2. **Task 2: AgentKernel shared bridge loop + test_agent.py adaptation** - `42880fd` (feat)

## Files Created/Modified
- `matmaster/core/context_compactor.py` - _summarize and compact_if_needed now async def, await self._summary_provider.chat()
- `matmaster/core/agent.py` - _sync_iterate_async/_sync_call_async bridge functions, run() creates shared bridge loop, _run_loop extracted, provider lifecycle management
- `tests/matmaster/core/test_context_compactor.py` - MockSummaryProvider/FailingSummaryProvider async, all compact_if_needed calls use await, TestEndToEndCompaction skipped
- `tests/matmaster/core/test_agent.py` - All providers (StreamingProvider, ToolCallingProvider, 7 inline providers) async, SpyCompactor/UsageSpyCompactor async compact_if_needed
- `tests/matmaster/core/conftest.py` - MockLLMProvider async (aenter/aexit/chat/chat_stream)
- `tests/matmaster/devshell/test_compaction_via_devshell.py` - MockSummaryProvider/FailingSummaryProvider async, all compact_if_needed calls use await, TestKernelIntegration skipped

## Decisions Made
- _call_llm creates its own temporary bridge loop when called directly (backward compat for tests that call kernel._call_llm without going through run())
- summary_provider lifecycle is entered/exited separately only when it differs from llm_provider (addresses review HIGH-1)
- Bridge functions clearly marked "Phase 13-16 transition period" for removal in Phase 17
- E2E/Kernel integration tests skipped rather than rewritten (per D-08: requires full Kernel async)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] _call_llm backward compatibility for direct test calls**
- **Found during:** Task 2 (test_agent.py verification)
- **Issue:** Tests calling kernel._call_llm() directly (TestCallLlmRetry, TestCallLlmUsageCapture) do not go through run(), so no bridge loop exists
- **Fix:** _call_llm creates temporary asyncio.new_event_loop() when _bridge_loop is None, closes it in finally block
- **Files modified:** matmaster/core/agent.py
- **Verification:** All 4 retry tests + 2 usage tests pass
- **Committed in:** 42880fd (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Necessary for backward compatibility with direct _call_llm test calls. No scope creep.

## Issues Encountered
None

## Known Stubs
None -- all implementation is complete, no placeholder data.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 13 fully complete: async LLMProvider Protocol + OpenAIProvider + ContextCompactor + Kernel bridge loop
- Phase 14 (if separate context compactor phase): ContextCompactor already async, nothing left
- Phase 17 (Kernel async): bridge functions (_sync_iterate_async, _sync_call_async) clearly marked for removal; _run_loop becomes async def, run() becomes async def
- Phase 18 (Exp async): Exp.build_runtime can now create async ContextCompactor, summary_provider lifecycle managed by Kernel

## Self-Check: PASSED

All 6 files verified present. Both commit hashes (cc3c6cd, 42880fd) found in git log.

---
*Phase: 13-llm-provider*
*Completed: 2026-03-27*
