---
phase: 12-protocol
plan: 02
subsystem: testing
tags: [protocol, async, pytest-asyncio, validation, mock, testing-infrastructure]

# Dependency graph
requires:
  - "12-01: async Protocol signatures (LLMProvider/Tool/Hook/EventHandler/ReplyQueueLike + BuiltinTool ABC)"
provides:
  - "validate_async_protocol() helper with async generator support"
  - "pytest-asyncio installed (auto mode) for async test execution"
  - "MockAsyncLLMProvider, MockAsyncTool, MockAsyncHook in tests/conftest.py"
  - "chat_with_retry fully removed from all test files (zero references)"
  - "Protocol tests updated for async signatures (test_llm_provider, test_hooks, test_builtin_base)"
affects: [13-kernel, 14-tools, 15-hooks, 16-integration, 17-exp]

# Tech tracking
tech-stack:
  added: [pytest-asyncio]
  patterns:
    - "validate_async_protocol() for runtime + test async/sync mismatch detection"
    - "_is_async_callable() checks both iscoroutinefunction and isasyncgenfunction"
    - "Async mock factories in root conftest.py for shared test infrastructure"
    - "Sync mock hooks used with sync run_* helpers (transitional Phase 12-15)"

key-files:
  created:
    - matmaster/validation.py
    - tests/conftest.py
    - tests/matmaster/test_validation.py
  modified:
    - pyproject.toml
    - tests/matmaster/core/conftest.py
    - tests/matmaster/core/test_agent.py
    - tests/matmaster/core/test_context_compactor.py
    - tests/matmaster/core/test_hooks.py
    - tests/matmaster/tools/test_builtin_base.py
    - tests/matmaster/types/test_llm_provider.py
    - tests/matmaster/types/test_runtime.py
    - tests/matmaster/providers/test_openai_provider.py
    - tests/matmaster/integration/test_e2e_mat_master.py
    - tests/matmaster/integration/test_e2e_minimal.py
    - tests/matmaster/integration/test_upstream_scenarios.py
    - tests/matmaster/integration/test_pipeline_alignment.py
    - tests/matmaster/integration/test_quota_pipeline.py
    - tests/matmaster/devshell/test_runner.py
    - tests/matmaster/devshell/test_integration.py

key-decisions:
  - "validate_async_protocol uses __protocol_attrs__ for Protocol introspection, skips properties via inspect.getattr_static"
  - "_is_async_callable accepts both iscoroutinefunction AND isasyncgenfunction -- critical for chat_stream async generator implementations"
  - "BaseHook direct tests changed to async (await calls); run_* helper tests kept sync with sync mock hooks (TrackingHook)"
  - "test_builtin_base _execute subclasses changed to async def; execute() transitional behavior documented (returns coroutine)"
  - "Deleted TestChatWithRetry (10 tests) and MissingRetryProvider from test_openai_provider and test_llm_provider"

patterns-established:
  - "Root conftest.py provides async mock factories for Protocol testing (MockAsyncLLMProvider, MockAsyncTool, MockAsyncHook)"
  - "Sync/async method boundary tests use validate_async_protocol for runtime detection"
  - "Transitional test patterns: sync hooks with sync run_* helpers, async hooks for direct BaseHook tests"

requirements-completed: [PROT-05, TEST-01]

# Metrics
duration: 13min
completed: 2026-03-26
---

# Phase 12 Plan 02: Test Infrastructure + chat_with_retry Cleanup Summary

**validate_async_protocol() helper with async generator detection, pytest-asyncio auto mode, async mock factories, chat_with_retry fully eliminated from 15 test files, Protocol tests adapted for async signatures**

## Performance

- **Duration:** 13 min
- **Started:** 2026-03-26T14:03:03Z
- **Completed:** 2026-03-26T14:16:30Z
- **Tasks:** 2
- **Files modified:** 19

## Accomplishments
- Created matmaster/validation.py with validate_async_protocol() and _is_async_callable() handling both coroutine functions and async generators
- Installed pytest-asyncio (1.3.0) as dev dependency with asyncio_mode=auto confirmed working
- Created 3 async mock factories (MockAsyncLLMProvider, MockAsyncTool, MockAsyncHook) in root tests/conftest.py
- Created 19 validation test cases covering sync mismatch detection, async passing, async generator detection, Guard sync preservation, and pytest-asyncio infrastructure
- Removed chat_with_retry from all 15 test files (zero grep matches across tests/ and matmaster/)
- Deleted TestChatWithRetry class (10 tests) from test_openai_provider.py
- Deleted MissingRetryProvider and TestChatWithRetryProtocol from test_llm_provider.py
- Updated test_llm_provider.py CompleteLLMProvider to async, tests to use await/async-for
- Updated test_hooks.py BaseHook direct tests to async with await
- Updated test_builtin_base.py _execute subclasses to async def with direct await testing
- 976 matmaster tests pass with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: pytest-asyncio + validation helper + async mock factories** - `03896f5` (feat)
2. **Task 2: chat_with_retry cleanup + Protocol test updates** - `05ad7cb` (fix)

## Files Created/Modified
- `matmaster/validation.py` - validate_async_protocol() helper with _is_async_callable() supporting async generators
- `tests/conftest.py` - MockAsyncLLMProvider, MockAsyncTool, MockAsyncHook + fixtures
- `tests/matmaster/test_validation.py` - 19 test cases for validation helper
- `pyproject.toml` - Added pytest-asyncio>=0.23.0 to dev optional dependencies
- `tests/matmaster/core/conftest.py` - Removed chat_with_retry from MockLLMProvider, added Phase 17 note
- `tests/matmaster/core/test_agent.py` - Removed chat_with_retry from 11 mock classes
- `tests/matmaster/core/test_context_compactor.py` - Removed chat_with_retry from 3 mock classes
- `tests/matmaster/core/test_hooks.py` - BaseHook tests async with await
- `tests/matmaster/tools/test_builtin_base.py` - _execute subclasses async, transitional behavior documented
- `tests/matmaster/types/test_llm_provider.py` - CompleteLLMProvider async, deleted chat_with_retry test classes
- `tests/matmaster/types/test_runtime.py` - Removed chat_with_retry from mock
- `tests/matmaster/providers/test_openai_provider.py` - Deleted TestChatWithRetry (10 tests) + test_has_chat_with_retry_method
- `tests/matmaster/integration/test_e2e_mat_master.py` - Removed chat_with_retry from 3 mocks
- `tests/matmaster/integration/test_e2e_minimal.py` - Removed chat_with_retry from mock
- `tests/matmaster/integration/test_upstream_scenarios.py` - Removed chat_with_retry from 3 mocks
- `tests/matmaster/integration/test_pipeline_alignment.py` - Removed chat_with_retry from mock
- `tests/matmaster/integration/test_quota_pipeline.py` - Removed chat_with_retry from 3 mocks
- `tests/matmaster/devshell/test_runner.py` - Removed chat_with_retry from mock
- `tests/matmaster/devshell/test_integration.py` - Removed chat_with_retry from 2 mocks

## Decisions Made
- **_is_async_callable dual check**: Both iscoroutinefunction and isasyncgenfunction must be accepted because Protocol stubs (async def ... -> AsyncIterator: ...) show as iscoroutinefunction=True, but implementations (async def: yield ...) show as isasyncgenfunction=True only.
- **BaseHook tests async, run_* tests sync**: BaseHook methods are now async def, so direct tests must await them. But run_* helpers are still sync def (Phase 15 changes them), so their tests use sync TrackingHook overrides which work fine via Python MRO.
- **BuiltinTool execute() transitional behavior documented**: Since execute() is sync def but calls async _execute() without await, it returns a coroutine object. Tests document this and test _execute directly. Phase 14 will unify.
- **Deleted 10 chat_with_retry tests in test_openai_provider.py**: These tested retry logic that no longer exists in OpenAIProvider. Retry responsibility moves to Kernel._call_llm() in Phase 13.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All async Protocol validation infrastructure is in place for Phase 13-18
- pytest-asyncio auto mode enables writing async def test_* in any test file
- MockAsyncLLMProvider/MockAsyncTool/MockAsyncHook available for all future async tests
- validate_async_protocol() ready for use in Exp.assemble() (Phase 18) for early mismatch detection
- chat_with_retry fully eliminated from codebase -- zero grep matches

## Self-Check: PASSED

All 3 created files verified present. Both commit hashes (03896f5, 05ad7cb) confirmed in git log.

---
*Phase: 12-protocol*
*Completed: 2026-03-26*
