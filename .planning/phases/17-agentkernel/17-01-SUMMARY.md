---
phase: 17-agentkernel
plan: 01
subsystem: core
tags: [asyncio, agent-kernel, async-await, pytest-asyncio]

# Dependency graph
requires:
  - phase: 13-llm-provider
    provides: "OpenAIProvider async context manager + chat_stream"
  - phase: 14-tool
    provides: "ToolRegistry.execute() async"
  - phase: 15-hook
    provides: "run_* helpers async + ConfirmationHook set_loop"
  - phase: 16-messagebus-eventrouter
    provides: "MessageBus async emit"
provides:
  - "AgentKernel.run() as async def returning KernelRunResult"
  - "Pure async execution loop (zero bridge code)"
  - "35 async kernel unit tests passing"
affects: [17-02-callers, 18-exp, 19-service]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "async with provider lifecycle management"
    - "asyncio.get_running_loop() for hook loop injection"
    - "await instead of _sync_call_async bridge"
    - "async for instead of _sync_iterate_async bridge"

key-files:
  created: []
  modified:
    - matmaster/core/agent.py
    - tests/matmaster/core/test_agent.py

key-decisions:
  - "Provider lifecycle uses async with (not manual __aenter__/__aexit__)"
  - "ExplodingTool in test changed from sync to async execute()"
  - "MagicMock import kept for potential future use"

patterns-established:
  - "Kernel methods: async def run/run_loop/call_llm/do_stream_llm"
  - "All async leaf nodes called via direct await (no bridge)"
  - "Static helper methods (_parse_arguments, _finish) remain sync (pure data)"

requirements-completed: [KERN-01, KERN-02, KERN-03, KERN-04, KERN-05, KERN-06, TEST-02]

# Metrics
duration: 9min
completed: 2026-03-28
---

# Phase 17 Plan 01: AgentKernel Async Summary

**AgentKernel 执行循环全面 async 化：run/run_loop/call_llm/do_stream_llm 改为 async def，移除全部 bridge 代码，35 个单元测试迁移为 async 并全部通过**

## Performance

- **Duration:** 9 min
- **Started:** 2026-03-28T17:44:36Z
- **Completed:** 2026-03-28T17:54:06Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- agent.py 从 550 行缩减到 ~470 行，移除全部 bridge 基础设施（_sync_call_async, _sync_iterate_async, _bridge_loop, _owns_loop）
- Provider 生命周期从 3 层嵌套 try/finally 简化为 async with 语法
- time.sleep(backoff) 替换为 await asyncio.sleep(backoff)，避免阻塞事件循环
- 35 个测试全部迁移为 async def，pytest-asyncio auto mode 自动识别，0.06s 内全部通过

## Task Commits

Each task was committed atomically:

1. **Task 1: AgentKernel async 化 -- agent.py 全面改造** - `2aa742e` (feat)
2. **Task 2: test_agent.py 全量 async 迁移** - `945202b` (test)

## Files Created/Modified
- `matmaster/core/agent.py` - Pure async AgentKernel execution loop, zero bridge code
- `tests/matmaster/core/test_agent.py` - 35 async tests covering all termination paths, guards, hooks, streaming, retries

## Decisions Made
- Provider 生命周期从手动 __aenter__/__aexit__ 改为 async with 语法（更简洁，异常安全）
- ConfirmationHook loop 注入从 asyncio.new_event_loop() 改为 asyncio.get_running_loop()（语义等价，无需自建 loop）
- ExplodingTool 测试的 execute() 从 sync 改为 async def（Rule 1: ToolRegistry.execute() 是 async，需要 await）
- summary_provider 双重进入保护逻辑保留（if sp is not spec.llm_provider 条件检查）

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] ExplodingTool.execute() changed to async def**
- **Found during:** Task 2 (test migration)
- **Issue:** ExplodingTool had sync `def execute()` which would not work correctly with async ToolRegistry.execute() in the new async kernel
- **Fix:** Changed to `async def execute()` to match the async Tool Protocol
- **Files modified:** tests/matmaster/core/test_agent.py
- **Verification:** All 35 tests pass
- **Committed in:** 945202b (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix)
**Impact on plan:** Necessary for async correctness. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- AgentKernel.run() is now async, breaking all sync callers
- Plan 02 MUST execute immediately to restore runnable state by adding bridge loops to Exp.run(), spawn_fn, and external test callers
- All kernel unit tests (35) pass independently

---
*Phase: 17-agentkernel*
*Completed: 2026-03-28*
