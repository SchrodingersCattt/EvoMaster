---
phase: 18-exp
verified: 2026-03-29T13:00:00Z
status: passed
score: 10/10 must-haves verified
re_verification: false
---

# Phase 18: Exp Async Lifecycle Verification Report

**Phase Goal:** Exp 三阶段生命周期 (assemble/build_runtime/run) 全部 async，SubAgent spawn 完整 async 链路打通
**Verified:** 2026-03-29T13:00:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Exp.assemble() 为 async def | VERIFIED | `async def assemble(self, ctx)` at exp.py:136 |
| 2 | Exp.build_runtime() 为 async def，内部 await assemble() | VERIFIED | `async def build_runtime` at exp.py:148; `spec = await self.assemble(ctx)` at exp.py:159 |
| 3 | Exp.run() 为 async def，直接 await build_runtime + kernel.run，无 bridge loop | VERIFIED | `async def run` at exp.py:273; `await self.build_runtime(...)` at exp.py:292; `await runtime.kernel.run(...)` at exp.py:304 |
| 4 | _run_cleanup_callbacks() 为 async def，dual dispatch (iscoroutinefunction + isawaitable) | VERIFIED | `async def _run_cleanup_callbacks` at exp.py:70; iscoroutinefunction check at line 80; isawaitable fallback at line 84 |
| 5 | run() try/finally 在 build_runtime 之前开始，cleanup 保证 | VERIFIED | exp.py:291 `try:` 紧接 `build_runtime` 调用，exp.py:308 `finally: await self._run_cleanup_callbacks()` |
| 6 | exp.py 内无 asyncio import，无 bridge loop | VERIFIED | `grep asyncio exp.py` 返回空；无 `new_event_loop` 引用 |
| 7 | spawn_fn 为 async def closure，内部 await child_exp.run() | VERIFIED | `async def spawn_fn` at exp.py:109; `result = await child_exp.run(...)` at exp.py:120 |
| 8 | SpawnTool.execute() 为 native async override，保留完整错误契约 | VERIFIED | `async def execute(self, arguments)` at spawn_tool.py:123；recursion guard (line 130)、参数校验 (lines 136-140)、exception catch (lines 142-145) 全部保留 |
| 9 | SpawnTool._execute() 保留为 ABC stub | VERIFIED | `def _execute` at spawn_tool.py:148，raises NotImplementedError |
| 10 | 所有相关测试通过（test_exp + spawn + integration + devshell） | VERIFIED | 67 passed (unit); 243 passed + 2 skipped (integration/devshell) |

**Score:** 10/10 truths verified

---

### Required Artifacts (Plan 01)

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `matmaster/core/exp.py` | Exp async lifecycle (assemble, build_runtime, run, cleanup) | VERIFIED | 4 methods are async def; `async def assemble` pattern confirmed at line 136 |
| `matmaster/core/exp.py` | async cleanup callback dispatch with isawaitable fallback | VERIFIED | `inspect.isawaitable` found at line 84 |
| `matmaster/types/runtime.py` | AgentRuntime.cleanup type accommodating async | VERIFIED | `cleanup: Callable[[], Any]` at runtime.py:114 |
| `src/services/agent_run_service.py` | Bridge loop covering build_runtime + kernel.run + cleanup | VERIFIED | `_loop.run_until_complete` covers build_runtime (line 432), kernel.run (line 477), cleanup in finally (line 486) |

### Required Artifacts (Plan 02)

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `matmaster/core/exp.py` | async spawn_fn closure in _make_spawn_fn using child_exp.run() | VERIFIED | `async def spawn_fn` at line 109; `await child_exp.run(...)` at line 120 |
| `matmaster/tools/builtin/spawn_tool.py` | SpawnTool async execute() override with full error contract | VERIFIED | `async def execute` at line 123; full error contract preserved |
| `tests/matmaster/tools/test_spawn_tool.py` | AsyncMock spawn_fn in tests | VERIFIED | `from unittest.mock import AsyncMock` at line 14; all test methods use AsyncMock |
| `tests/matmaster/integration/test_subagent_spawn.py` | Async spawn integration tests | VERIFIED | `from unittest.mock import AsyncMock` at line 18; `patch.object(Exp, "run", new_callable=AsyncMock)` throughout |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `exp.py:build_runtime` | `exp.py:assemble` | `await self.assemble(ctx)` | VERIFIED | exp.py:159 |
| `exp.py:run` | `exp.py:build_runtime` | `await self.build_runtime(...)` | VERIFIED | exp.py:292 |
| `exp.py:run` | `agent.py:kernel.run` | `await runtime.kernel.run(...)` | VERIFIED | exp.py:304 |
| `agent_run_service.py` | `exp.py:build_runtime` | `_loop.run_until_complete(exp.build_runtime(...))` | VERIFIED | agent_run_service.py:432 |
| `exp.py:_make_spawn_fn:spawn_fn` | `exp.py:run` | `await child_exp.run(ctx, task, ...)` | VERIFIED | exp.py:120 |
| `spawn_tool.py:execute` | spawn_fn (async closure) | `await self._spawn_fn(...)` | VERIFIED | spawn_tool.py:143 |

---

### Data-Flow Trace (Level 4)

Not applicable — Phase 18 is a pure async refactor of execution infrastructure. No new data rendering components were introduced; the phase transforms existing synchronous lifecycle methods to async. Data flow paths are unchanged structurally.

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Exp unit tests pass (async lifecycle + spawn) | `uv run pytest tests/matmaster/core/test_exp.py tests/matmaster/tools/test_spawn_tool.py tests/matmaster/integration/test_subagent_spawn.py -q` | 67 passed | PASS |
| Integration + DevShell tests pass (bridge loop + full pipeline) | `uv run pytest tests/matmaster/integration/ tests/matmaster/devshell/ -q` | 243 passed, 2 skipped | PASS |
| exp.py 无 asyncio import，无 bridge loop 残留 | `grep asyncio matmaster/core/exp.py` | 无输出 | PASS |
| exp.py 无 new_event_loop | `grep new_event_loop matmaster/core/exp.py` | 无输出 | PASS |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| EXPL-01 | 18-01 | Exp.assemble() 改为 async def | SATISFIED | `async def assemble` at exp.py:136 |
| EXPL-02 | 18-01 | Exp.build_runtime() 改为 async def | SATISFIED | `async def build_runtime` at exp.py:148 |
| EXPL-03 | 18-01 | Exp.run() 改为 async def，内部 await kernel.run() | SATISFIED | `async def run` at exp.py:273; `await runtime.kernel.run(...)` at exp.py:304 |
| EXPL-04 | 18-02 | SubAgent spawn 完整 async 链路（async spawn_fn → async Exp.run() → async kernel） | SATISFIED | `async def spawn_fn` + `await child_exp.run()` + `await self._spawn_fn(...)` in SpawnTool.execute() |

**Note:** REQUIREMENTS.md 状态表中 EXPL-01、EXPL-02、EXPL-03 仍标记为 "Pending"（EXPL-04 标记为 "Complete"）。这是文档未同步更新，不影响实现正确性。建议在 Phase 19 开始前更新 REQUIREMENTS.md 将三项标记为 Complete。

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `src/services/agent_run_service.py` | 432-486 | build_runtime 和 kernel.run 分开调用，而非通过 Exp.run() 统一调用 | INFO | 符合 Plan 01 设计意图（service 层保持 split-call 模式，bridge loop 在 Plan 19 整体重构）；cleanup 已移入 finally，无功能缺陷 |
| `.planning/REQUIREMENTS.md` | 133-135 | EXPL-01/02/03 状态仍为 Pending | INFO | 文档未同步，不影响代码正确性 |

无阻塞性或警告级 anti-pattern。

---

### Human Verification Required

无需人工验证。所有核心行为均可通过代码静态分析和测试套件程序化验证。

---

### Gaps Summary

无 gaps。Phase 18 所有目标均已达成：

- Plan 01（EXPL-01/02/03）：Exp 三阶段生命周期全部为 async def，cleanup 升级为 dual dispatch，service 层和 DevShell bridge loop 完整覆盖 build_runtime + kernel.run + cleanup，try/finally 保证 partial build 时 cleanup 也执行。

- Plan 02（EXPL-04）：spawn_fn 为 async closure 复用 Exp.run() 完整生命周期，SpawnTool.execute() native async override，exp.py 内零 bridge loop 残留，所有 spawn 相关测试迁移为 AsyncMock。

全量测试通过：310 passed, 2 skipped。

---

_Verified: 2026-03-29T13:00:00Z_
_Verifier: Claude (gsd-verifier)_
