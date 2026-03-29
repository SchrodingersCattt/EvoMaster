---
phase: 21-async-leaf-io-cleanup
plan: 01
subsystem: tools, providers
tags: [asyncio, subprocess, bash, openai, protocol-cleanup]

# Dependency graph
requires: []
provides:
  - BashTool dual-path execute (native async subprocess for LocalSession)
  - LLMProvider Protocol without chat_with_retry (cleaned)
  - OpenAIProvider without dead code (chat_with_retry removed)
affects: [22-async-kernel, tools, providers]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "BashTool dual-path execute: isinstance check on session type to choose async vs sync path"
    - "asyncio.run() inside sync execute() to bridge sync call to async subprocess"

key-files:
  created: []
  modified:
    - matmaster/tools/builtin/bash_tool.py
    - matmaster/providers/openai_provider.py
    - matmaster/types/llm_provider.py
    - tests/matmaster/tools/test_bash_tool.py
    - tests/matmaster/providers/test_openai_provider.py
    - tests/matmaster/types/test_llm_provider.py
    - tests/matmaster/core/conftest.py
    - tests/matmaster/core/test_agent.py
    - tests/matmaster/core/test_context_compactor.py

key-decisions:
  - "BashTool.execute() override is sync (not async) using asyncio.run() internally, preserving backward compatibility with the current sync execution model"
  - "LLMProvider Protocol updated to remove chat_with_retry (Rule 3 deviation: Protocol still had it, blocking provider cleanup)"

patterns-established:
  - "Dual-path tool execution: isinstance check on session type to choose native async vs sync fallback"

requirements-completed: [TOOL-02]

# Metrics
duration: 19min
completed: 2026-03-29
---

# Phase 21 Plan 01: Leaf IO Cleanup Summary

**BashTool native async subprocess via create_subprocess_exec for LocalSession, chat_with_retry removed from LLMProvider Protocol and OpenAIProvider**

## Performance

- **Duration:** 19 min
- **Started:** 2026-03-29T16:39:35Z
- **Completed:** 2026-03-29T16:58:56Z
- **Tasks:** 2
- **Files modified:** 17

## Accomplishments
- BashTool now uses asyncio.create_subprocess_exec for evomaster LocalSession, eliminating thread-pool overhead for local command execution
- Removed 73 lines of orphaned chat_with_retry from OpenAIProvider and corresponding LLMProvider Protocol method
- Cleaned chat_with_retry from 13 test files (526 lines of dead mock code removed)
- 941 tests pass with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: BashTool native async subprocess dual-path (TDD)**
   - `acc7645` (test) - RED: failing tests for async subprocess path
   - `c8f3ea6` (feat) - GREEN: implement dual-path execute with create_subprocess_exec
2. **Task 2: Remove OpenAIProvider orphaned chat_with_retry + full regression** - `3e3209e` (fix)

## Files Created/Modified
- `matmaster/tools/builtin/bash_tool.py` - Added execute() override with dual-path: async subprocess for LocalSession, sync fallback otherwise
- `matmaster/providers/openai_provider.py` - Removed chat_with_retry method (73 lines) and import time
- `matmaster/types/llm_provider.py` - Removed chat_with_retry from LLMProvider Protocol
- `tests/matmaster/tools/test_bash_tool.py` - Added TestBashToolAsyncSubprocess with 5 tests
- `tests/matmaster/providers/test_openai_provider.py` - Removed TestChatWithRetry class (190 lines)
- `tests/matmaster/types/test_llm_provider.py` - Removed MissingRetryProvider, TestChatWithRetryProtocol, chat_with_retry from CompleteLLMProvider
- `tests/matmaster/core/conftest.py` - Removed chat_with_retry from MockLLMProvider
- `tests/matmaster/core/test_agent.py` - Removed chat_with_retry from 12 mock providers
- `tests/matmaster/core/test_context_compactor.py` - Removed chat_with_retry from 3 mock providers
- `tests/matmaster/integration/*.py` - Removed chat_with_retry from 5 integration test mock providers
- `tests/matmaster/devshell/*.py` - Removed chat_with_retry from 3 devshell test mock providers

## Decisions Made
- BashTool.execute() override is sync (not async as plan specified), using asyncio.run() internally. Rationale: the current execution model (ToolRegistry -> tool.execute) is fully synchronous. Making execute() async would break all existing callers. The plan's async subprocess goal is achieved via asyncio.run() bridge.
- LLMProvider Protocol updated to remove chat_with_retry (Rule 3 auto-fix). The plan stated the Protocol was already updated in Phase 12, but it was not. Removing from Protocol was necessary to maintain isinstance conformance when removing from OpenAIProvider.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] LLMProvider Protocol still had chat_with_retry**
- **Found during:** Task 2 (Remove chat_with_retry from OpenAIProvider)
- **Issue:** Plan stated "chat_with_retry was removed from the LLMProvider Protocol in Phase 12" but the Protocol at matmaster/types/llm_provider.py still defined it. Removing from OpenAIProvider alone would break isinstance(provider, LLMProvider) check.
- **Fix:** Removed chat_with_retry from LLMProvider Protocol, updated all 13 test files with mock providers that implemented it, removed dedicated Protocol tests for chat_with_retry.
- **Files modified:** matmaster/types/llm_provider.py, tests/matmaster/types/test_llm_provider.py, tests/matmaster/core/conftest.py, tests/matmaster/core/test_agent.py, tests/matmaster/core/test_context_compactor.py, tests/matmaster/integration/test_*.py (5 files), tests/matmaster/devshell/test_*.py (2 files)
- **Verification:** 941 tests pass, isinstance(provider, LLMProvider) works correctly
- **Committed in:** 3e3209e (Task 2 commit)

**2. [Rule 3 - Blocking] BashTool.execute() kept sync to preserve backward compatibility**
- **Found during:** Task 1 (BashTool implementation)
- **Issue:** Plan specified async def execute() override, but base class BuiltinTool.execute() is sync and all callers (ToolRegistry.execute, existing tests) call it synchronously. An async override would return a coroutine instead of a string, breaking everything.
- **Fix:** Kept execute() sync, used asyncio.run() internally to bridge to _execute_async(). The async subprocess goal is fully achieved.
- **Files modified:** matmaster/tools/builtin/bash_tool.py
- **Verification:** All 12 BashTool tests pass (7 existing + 5 new), create_subprocess_exec used for LocalSession
- **Committed in:** c8f3ea6 (Task 1 GREEN commit)

---

**Total deviations:** 2 auto-fixed (2 blocking)
**Impact on plan:** Both deviations were necessary for correctness. The Protocol cleanup was more extensive than planned but purely mechanical (removing dead code). The sync execute() bridge achieves identical async subprocess behavior.

## Issues Encountered
None beyond the documented deviations.

## User Setup Required
None - no external service configuration required.

## Known Stubs
None - all functionality is fully wired.

## Next Phase Readiness
- BashTool async path ready for future async kernel integration (switch asyncio.run() to direct await when kernel becomes async)
- LLMProvider Protocol is clean: only chat() + chat_stream() required
- All mock providers across test suite updated -- no latent chat_with_retry references remain

## Self-Check: PASSED

All created/modified files verified present. All commit hashes (acc7645, c8f3ea6, 3e3209e) verified in git log.

---
*Phase: 21-async-leaf-io-cleanup*
*Completed: 2026-03-29*
