---
phase: 02-agent-kernel
plan: 03
subsystem: kernel
tags: [llm-provider, retry, exponential-backoff, openai, protocol]

# Dependency graph
requires:
  - phase: 02-agent-kernel (plans 01, 02)
    provides: LLMProvider Protocol with chat/chat_stream, OpenAIProvider implementation
provides:
  - LLMProvider Protocol with chat_with_retry() method (max_retries, retry_delay)
  - OpenAIProvider explicit retry with exponential backoff
  - Zero SDK-level retry dependency (max_retries=0 to SDK)
affects: [03-exp-layer, 04-playground]

# Tech tracking
tech-stack:
  added: []
  patterns: [protocol-level-retry, exponential-backoff, non-retryable-error-classification]

key-files:
  created: []
  modified:
    - matmaster/kernel/llm_provider.py
    - matmaster/kernel/openai_provider.py
    - tests/matmaster/kernel/test_llm_provider.py
    - tests/matmaster/kernel/test_openai_provider.py
    - tests/matmaster/kernel/conftest.py
    - tests/matmaster/kernel/test_kernel.py
    - tests/matmaster/contracts/test_runtime.py

key-decisions:
  - "Retry at Protocol level, not SDK level -- every provider implements own retry logic"
  - "SDK max_retries=0 explicitly disables SDK retry, chat_with_retry handles all retry"
  - "Non-retryable errors: auth (401), permission denied, context length exceeded -- raise immediately"
  - "Retryable errors: connection, timeout, rate limit (429), server error (500)"

patterns-established:
  - "Protocol-level retry: chat_with_retry with exponential backoff at LLMProvider Protocol"
  - "Non-retryable error classification: auth and context length errors bypass retry"

requirements-completed: [LLMP-01]

# Metrics
duration: 5min
completed: 2026-03-22
---

# Phase 2 Plan 3: LLMProvider chat_with_retry Gap Closure Summary

**LLMProvider Protocol extended with chat_with_retry() using explicit exponential backoff, SDK retry disabled**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-21T17:13:39Z
- **Completed:** 2026-03-21T17:19:26Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments
- LLMProvider Protocol now defines 3 methods: chat(), chat_with_retry(), chat_stream()
- OpenAIProvider implements explicit retry with exponential backoff (delay * 2^attempt)
- Non-retryable errors (auth 401, context length) raise immediately without retry
- All 195 tests pass (177 existing + 18 new), zero regressions

## Task Commits

Each task was committed atomically (TDD: RED then GREEN):

1. **Task 1: Add chat_with_retry to LLMProvider Protocol** - `54325bf` (test: RED), `547ca95` (feat: GREEN)
2. **Task 2: Implement chat_with_retry in OpenAIProvider** - `8307faf` (test: RED), `1668066` (feat: GREEN)

## Files Created/Modified
- `matmaster/kernel/llm_provider.py` - Added chat_with_retry to LLMProvider Protocol with max_retries and retry_delay params
- `matmaster/kernel/openai_provider.py` - Implemented chat_with_retry with exponential backoff, SDK max_retries=0
- `tests/matmaster/kernel/test_llm_provider.py` - TestChatWithRetryProtocol, CompleteLLMProvider updated, MissingRetryProvider
- `tests/matmaster/kernel/test_openai_provider.py` - TestChatWithRetry (11 tests), updated construction tests
- `tests/matmaster/kernel/conftest.py` - MockLLMProvider updated with chat_with_retry
- `tests/matmaster/kernel/test_kernel.py` - StreamingProvider, ToolCallingProvider, inline providers updated
- `tests/matmaster/contracts/test_runtime.py` - _MockLLMProvider updated with chat_with_retry

## Decisions Made
- Retry at Protocol level ensures every LLM provider (not just OpenAI) handles retry consistently
- SDK max_retries set to 0 explicitly to prevent double-retry behavior
- chat_with_retry accepts optional overrides (max_retries, retry_delay) per call, defaults from constructor
- BadRequestError with "context" + "length" or "token" treated as non-retryable

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated mock providers in test_kernel.py and test_runtime.py**
- **Found during:** Task 1 (Protocol update)
- **Issue:** Protocol change broke existing mock providers (StreamingProvider, ToolCallingProvider, CancelAfterFirstTurnProvider, TwoPhaseProvider, _MockLLMProvider) that lacked chat_with_retry
- **Fix:** Added chat_with_retry to all mock providers that satisfy LLMProvider Protocol
- **Files modified:** tests/matmaster/kernel/test_kernel.py, tests/matmaster/contracts/test_runtime.py
- **Verification:** All 195 tests pass
- **Committed in:** 547ca95 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - bug fix)
**Impact on plan:** Essential fix to maintain test suite compatibility after Protocol change. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 2 (Agent Kernel) fully complete with all 3 plans executed
- LLMProvider Protocol complete with chat() + chat_with_retry() + chat_stream()
- Ready for Phase 3 (Exp Layer) which will use kernel for agent orchestration

## Self-Check: PASSED

All 8 files verified present. All 4 commits verified in git log.

---
*Phase: 02-agent-kernel*
*Completed: 2026-03-22*
