---
phase: 13-llm-provider
plan: 01
subsystem: providers
tags: [async, openai, asyncio, httpx, protocol, context-manager]

# Dependency graph
requires:
  - phase: 11-subagent-spawn
    provides: stable LLMProvider Protocol and OpenAIProvider implementation
provides:
  - async LLMProvider Protocol with __aenter__/__aexit__ lifecycle contract
  - AsyncOpenAI-based OpenAIProvider with lazy client init
  - validate_async_protocol helper for Protocol conformance checking
  - pytest-asyncio test infrastructure
affects: [14-context-compactor, 17-kernel-async, 18-exp-async]

# Tech tracking
tech-stack:
  added: [pytest-asyncio, openai.AsyncOpenAI, httpx.AsyncClient]
  patterns: [async-context-manager-lifecycle, lazy-client-init, _ensure_client-guard]

key-files:
  created:
    - matmaster/validation.py
  modified:
    - matmaster/types/llm_provider.py
    - matmaster/providers/openai_provider.py
    - tests/matmaster/providers/test_openai_provider.py
    - tests/matmaster/providers/test_llm_factory.py
    - pyproject.toml

key-decisions:
  - "LLMProvider Protocol declares __aenter__/__aexit__ as formal contract for lifecycle management"
  - "OpenAIProvider __init__ stores params only, __aenter__ creates AsyncOpenAI + httpx.AsyncClient"
  - "chat_with_retry retained as sync legacy bridge, calls asyncio.get_event_loop().run_until_complete"
  - "validate_async_protocol helper created to bridge @runtime_checkable gap for async/sync distinction"

patterns-established:
  - "async-context-manager-lifecycle: Provider init stores params, __aenter__ creates resources, __aexit__ closes"
  - "_ensure_client guard: RuntimeError if provider methods called outside async context"
  - "async mock pattern: _async_iter helper + AsyncMock + direct _client injection (no constructor patches)"

requirements-completed: [LLMP-01, LLMP-02, LLMP-03]

# Metrics
duration: 7min
completed: 2026-03-27
---

# Phase 13 Plan 01: LLM Provider Async Summary

**OpenAIProvider async 改造 + LLMProvider Protocol 扩展 __aenter__/__aexit__ 生命周期契约，使用 AsyncOpenAI client，全部 62 tests 通过**

## Performance

- **Duration:** 7 min
- **Started:** 2026-03-26T18:18:25Z
- **Completed:** 2026-03-26T18:25:22Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- LLMProvider Protocol 正式声明 __aenter__/__aexit__ 作为 async context manager 生命周期契约
- OpenAIProvider 使用 AsyncOpenAI client，chat/chat_stream 为 async 方法
- 全部 55 个 provider 测试迁移为 async + 5 个新生命周期测试 + 7 个 factory 测试 = 62 tests pass

## Task Commits

Each task was committed atomically:

1. **Task 1: LLMProvider Protocol + OpenAIProvider async 实现** - `9d7885c` (feat)
2. **Task 2: Provider 测试迁移 async + 新增生命周期测试** - `67df1c5` (test)

## Files Created/Modified
- `matmaster/types/llm_provider.py` - LLMProvider Protocol: async chat/chat_stream + __aenter__/__aexit__, removed chat_with_retry from Protocol
- `matmaster/providers/openai_provider.py` - AsyncOpenAI-based provider with lazy init, async context manager lifecycle
- `matmaster/validation.py` - validate_async_protocol helper for runtime Protocol conformance checking
- `tests/matmaster/providers/test_openai_provider.py` - 55 async tests + 5 new TestAsyncContextManager lifecycle tests
- `tests/matmaster/providers/test_llm_factory.py` - 7 factory tests, no patches needed (lazy init)
- `pyproject.toml` - Added pytest-asyncio to dev dependencies

## Decisions Made
- LLMProvider Protocol 不再包含 chat_with_retry -- retry 逻辑移到 Kernel 层（per CONTEXT D-01）
- chat_with_retry 保留在 OpenAIProvider 作为 sync legacy 桥接，未来 Kernel async 化后移除
- validate_async_protocol 从 Phase 12 主仓库带入 worktree（worktree 基于不同分支）

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Created matmaster/validation.py**
- **Found during:** Task 1 (verification script)
- **Issue:** Plan verify script imports validate_async_protocol from matmaster.validation, but this file only exists in main repo (Phase 12 work on different branch)
- **Fix:** Copied validate_async_protocol implementation from main repo into worktree
- **Files modified:** matmaster/validation.py (created)
- **Verification:** Verify script passes, all Protocol checks pass
- **Committed in:** 9d7885c (Task 1 commit)

**2. [Rule 3 - Blocking] Added pytest-asyncio to dev dependencies**
- **Found during:** Task 1 (pre-verification)
- **Issue:** async tests require pytest-asyncio, not in pyproject.toml dev dependencies
- **Fix:** Added pytest-asyncio>=0.25.0 to [project.optional-dependencies] dev
- **Files modified:** pyproject.toml
- **Verification:** uv sync --extra dev installs correctly, asyncio_mode=auto in setup.cfg
- **Committed in:** 9d7885c (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (2 blocking)
**Impact on plan:** Both necessary for test infrastructure. No scope creep.

## Issues Encountered
None

## Known Stubs
None -- all implementation is complete, no placeholder data.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- async LLMProvider Protocol ready for downstream consumers
- ContextCompactor async 改造 (Phase 13 Plan 02) can proceed: only needs to await provider.chat()
- Kernel/Exp async 改造 (Phase 17-18) can use async with provider pattern

## Self-Check: PASSED

All 6 files verified present. Both commit hashes (9d7885c, 67df1c5) found in git log.

---
*Phase: 13-llm-provider*
*Completed: 2026-03-27*
