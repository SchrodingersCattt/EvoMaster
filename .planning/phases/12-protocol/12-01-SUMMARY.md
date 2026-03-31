---
phase: 12-protocol
plan: 01
subsystem: core
tags: [protocol, async, typing, llm-provider, tool, hook, guard, abc]

# Dependency graph
requires: []
provides:
  - "async LLMProvider Protocol (chat, chat_stream) -- no chat_with_retry"
  - "async Tool Protocol (execute)"
  - "async BuiltinTool ABC (_execute abstractmethod)"
  - "async Hook Protocol (7 methods) + async BaseHook defaults"
  - "async EventHandler Protocol (handle)"
  - "async ReplyQueueLike Protocol (put_content, put_cancel, get)"
  - "Guard Protocol unchanged (evaluate stays sync)"
affects: [13-kernel, 14-tools, 15-hooks, 16-integration, 17-exp]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "async Protocol signatures as contract foundation for v2.0"
    - "Signature-only changes: Protocol async but callers/implementations stay sync until their phase"

key-files:
  created: []
  modified:
    - matmaster/types/llm_provider.py
    - matmaster/tools/tool_registry.py
    - matmaster/tools/builtin/base.py
    - matmaster/core/hooks.py
    - matmaster/integration/event_router.py
    - matmaster/hooks/confirmation.py
    - matmaster/providers/openai_provider.py

key-decisions:
  - "chat_with_retry removed from both Protocol and OpenAIProvider -- retry logic moves to Kernel._call_llm() in Phase 13"
  - "Guard.evaluate stays sync -- CPU-bound evaluation, no I/O, no async benefit"
  - "run_* helpers and EventEmitterHook stay sync -- 13+ AgentKernel call sites depend on sync; changed together in Phase 15"
  - "BuiltinTool.execute() body unchanged (no await) -- ToolRegistry.execute() still calls it synchronously; Phase 14+17 will unify"
  - "Removed unused time import from openai_provider.py after chat_with_retry deletion"

patterns-established:
  - "Protocol-first async migration: change Protocol signatures first, implementations follow in later phases"
  - "Layered compatibility: sync callers can invoke async methods without await (returns coroutine object, no TypeError in Python)"

requirements-completed: [PROT-01, PROT-02, PROT-03, PROT-04]

# Metrics
duration: 4min
completed: 2026-03-26
---

# Phase 12 Plan 01: Protocol Async Signatures Summary

**6 Protocol/ABC async signature changes (LLMProvider/Tool/Hook/EventHandler/ReplyQueueLike + BuiltinTool ABC) + chat_with_retry removal from Protocol and implementation**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-26T13:51:51Z
- **Completed:** 2026-03-26T13:55:31Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments
- LLMProvider Protocol reduced to 2 async methods (chat, chat_stream), chat_with_retry eliminated
- chat_stream return type changed from Iterator to AsyncIterator
- Tool Protocol execute and BuiltinTool ABC _execute changed to async def
- Hook Protocol and BaseHook: all 7 methods changed to async def
- EventHandler and ReplyQueueLike Protocols changed to async def
- OpenAIProvider.chat_with_retry implementation deleted (63 lines removed)
- Guard Protocol confirmed unchanged (evaluate stays sync)

## Task Commits

Each task was committed atomically:

1. **Task 1: LLMProvider + Tool + BuiltinTool + Guard Protocol/ABC signatures** - `a070a03` (feat)
2. **Task 2: Hook + EventHandler + ReplyQueueLike + chat_with_retry deletion** - `93b6bbf` (feat)

## Files Created/Modified
- `matmaster/types/llm_provider.py` - async LLMProvider Protocol (chat, chat_stream only), AsyncIterator return type
- `matmaster/tools/tool_registry.py` - async Tool Protocol execute; ToolRegistry unchanged
- `matmaster/tools/builtin/base.py` - async _execute abstractmethod; execute() body unchanged
- `matmaster/core/hooks.py` - async Hook Protocol + async BaseHook defaults; run_* helpers and EventEmitterHook unchanged
- `matmaster/integration/event_router.py` - async EventHandler Protocol; EventRouter class unchanged
- `matmaster/hooks/confirmation.py` - async ReplyQueueLike Protocol; ConfirmationHook unchanged
- `matmaster/providers/openai_provider.py` - chat_with_retry removed, docstrings/comments updated

## Decisions Made
- chat_with_retry removed from Protocol and OpenAIProvider: retry logic responsibility moves to Kernel._call_llm() (Phase 13). The retry parameters (max_retries, retry_delay) remain as OpenAIProvider properties for Kernel to read.
- Guard.evaluate stays sync: guard evaluation is CPU-bound pattern matching with no I/O, async adds no benefit.
- run_* helpers stay sync: AgentKernel calls them synchronously in 13+ locations. Changing them requires changing Kernel simultaneously (Phase 15).
- BuiltinTool.execute() body unchanged: ToolRegistry.execute() calls tool.execute() synchronously. Both change together in Phase 14+17.
- Removed unused `time` import from openai_provider.py (was only used by chat_with_retry).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Removed unused `time` import from openai_provider.py**
- **Found during:** Task 2 (chat_with_retry deletion)
- **Issue:** After deleting chat_with_retry, the `import time` became unused (time.sleep was only used in retry backoff)
- **Fix:** Removed `import time` line
- **Files modified:** matmaster/providers/openai_provider.py
- **Verification:** File imports verified clean
- **Committed in:** 93b6bbf (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 missing critical -- dead import cleanup)
**Impact on plan:** Minor cleanup, no scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All 6 Protocol signatures now declare async contracts for v2.0
- Phase 12 Plan 02 can proceed with test infrastructure (pytest-asyncio) to verify these signatures
- Phase 13 (Kernel) can implement async execution against these Protocol signatures
- Phase 14 (Tools) can implement async tool execution against the async Tool Protocol
- Phase 15 (Hooks) can convert run_* helpers and EventEmitterHook to async

## Self-Check: PASSED

All 8 files verified present. Both commit hashes (a070a03, 93b6bbf) confirmed in git log.

---
*Phase: 12-protocol*
*Completed: 2026-03-26*
