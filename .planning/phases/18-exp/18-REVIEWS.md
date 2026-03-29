---
phase: 18
reviewers: [codex]
reviewed_at: 2026-03-29T19:07:00+08:00
plans_reviewed: [18-01-PLAN.md, 18-02-PLAN.md]
---

# Cross-AI Plan Review — Phase 18

## Codex Review

以下评审是基于当前实现核对后给出的，主要看了 matmaster/core/exp.py、matmaster/tools/builtin/spawn_tool.py、matmaster/tools/builtin/base.py、src/services/agent_run_service.py、matmaster/devshell/runner.py、matmaster/devshell/repl.py 以及相关测试。

### Plan 18-01: Exp Core 4 Methods Async + AgentRuntime Type Update + Service/DevShell Bridge

**Summary**
这个计划的大方向是对的，拆分也合理：先把 Exp 生命周期异步化，再用临时 bridge 保住现有同步入口，符合 Phase 17 之后的迁移节奏。但按当前文字直接执行，最大的问题是 async cleanup 没有被完整纳入 bridge 范围，而且测试影响面被低估了，所以它更像是正确方向上的半成品计划，不是可以直接低风险落地的执行稿。

**Strengths**
- 先做 assemble/build_runtime/run，后做 spawn 链路，依赖顺序清晰，符合 EXPL-01 到 EXPL-03 的自然边界。
- 保留 agent_run_service 和 DevShell 的临时同步 bridge，是控制改动半径的好做法，避免把 service 层重构提前到本 phase。
- assemble() 和 build_runtime() 一起改成 async interface，和前面 Protocol 层的 hard cut 风格一致，不会留下双接口债务。
- 把 cleanup 机制一并纳入这波迁移，而不是留到后续补丁，方向上是正确的。

**Concerns**
- **HIGH**: 计划没有显式处理 async cleanup 在同步调用点的落地。现在 agent_run_service.py 直接调用 `exp._run_cleanup_callbacks()`，而 devshell/runner.py 和 devshell/repl.py 直接调用 `runtime.cleanup()`。如果这些变成 async def 但没有进入同一个 bridge loop，cleanup 会根本不执行，甚至产生未 awaited coroutine。
- **HIGH**: `Exp.run()` 的 finally 语义需要覆盖 `build_runtime()` 失败场景，而计划描述里没有写清楚。当前 `build_runtime()` 内部已经会注册 cleanup（tracker.clear 和 connector.cleanup）。如果 `await self.build_runtime(...)` 之后才进入 try/finally，中途抛错会泄漏资源。
- **MEDIUM**: 测试范围明显少估了。除了 test_exp.py 和 4 个 integration 文件，当前还有 test_compaction_via_devshell.py、test_guard_injection.py、test_repl.py、test_runtime.py 等都直接依赖同步接口。
- **MEDIUM**: AgentRuntime.cleanup 改成 `Callable[[], Any]` 过于宽松，会把错误用法静默藏掉。实际上更接近 async callable，而不是任意返回值 callable。
- **LOW**: `inspect.iscoroutinefunction(cb)` 不是最稳的 async callback 检测方式。对 partial、decorator 包装或实现了 async `__call__` 的对象，可能误判。

**Suggestions**
- 把 cleanup bridge 写成计划中的显式任务，正确的覆盖范围应该是 build_runtime -> kernel.run -> cleanup -> close loop。
- 在 Exp.run() 里采用 `runtime = None` 的结构，try 要从 `await build_runtime()` 之前开始，finally 里统一 `await self._run_cleanup_callbacks()`，这样 partial build failure 也能清理。
- AgentRuntime.cleanup 建议显式表达 async 契约，至少用 `Callable[[], Awaitable[None]]` 或 sync/async union。
- cleanup 执行建议采用 `result = cb(); if inspect.isawaitable(result): await result`，比只看 `iscoroutinefunction` 更稳。
- 测试计划里应明确列出额外受影响文件，尤其是 test_repl.py、test_runtime.py、test_guard_injection.py 和 test_compaction_via_devshell.py。

**Risk Assessment**: MEDIUM-HIGH

---

### Plan 18-02: SubAgent Spawn Async Chain

**Summary**
这个计划抓住了最关键的点：去掉 _make_spawn_fn 里的手工 event loop，并让 SpawnTool 走真正的 async 链路，这与 EXPL-04 是一致的。但当前方案存在两个实质性缺口：一是 SpawnTool.execute() 直接重写后会丢掉现有错误包装契约，二是计划文本并没有真正实现 success criteria 里要求的 spawn_fn -> child Exp.run() -> child kernel.run() 这一层次。

**Strengths**
- Wave 2 依赖 18-01，顺序合理，避免在 build_runtime() 还是同步时就改 spawn。
- 去掉 _make_spawn_fn 里的 asyncio.new_event_loop()，确实能消除当前最明显的 event loop 阻塞点。
- SpawnTool 不再走 BuiltinTool.execute() 里的 to_thread，从性能和语义上都更适合真正的 async child run。
- stop_event 传播仍然被纳入测试关注，方向正确。

**Concerns**
- **HIGH**: 如果 SpawnTool.execute() 只是简单 await self._spawn_fn(...)，会绕过 BuiltinTool.execute() 当前的异常捕获和 Error: 返回语义。现有测试明确依赖这个契约。这里不是单测细节，而是运行时工具失败语义会变。
- **HIGH**: 计划没有明确保留 spawn_tool.py 里已有的 recursion guard 和参数校验逻辑。如果重写 execute() 时只保留 await 调用，spawn_fn is None、空 exp_name、空 task 这些现有行为就会回退。
- **HIGH/MEDIUM**: 计划文本和 phase success criteria 不一致。现在写的是 await child_exp.build_runtime() 再 await child_runtime.kernel.run()，但目标要求的是 async spawn_fn -> await child Exp.run() -> await child kernel.run()。这两者差别不只是封装层次。
- **MEDIUM**: 类型更新不完整。_make_spawn_fn 返回类型、SpawnTool.__init__ 里的 spawn_fn 注解都需要改成 Awaitable[str] 语义。
- **MEDIUM**: 测试迁移计划漏掉了 test_subagent_spawn.py 里仍然使用 Mock 的 spawn_fn 的测试用例。
- **LOW**: 当前测试没有一个真正证明 spawn 期间 event loop 仍可调度其他 coroutine，不阻塞 event loop 这一成功标准缺少直接证据。

**Suggestions**
- SpawnTool.execute() 重写后仍要保留原契约：先做 guard 和参数校验，再 try/except 包住 await self._spawn_fn(...)，继续返回 Error: ... 字符串并打日志。
- 如果要严格满足 roadmap，建议让 _make_spawn_fn 调 await child_exp.run(...)，而不是再手写一份 child build/run/cleanup 流程。
- 如果出于最小改动决定保留 child build_runtime + kernel.run 直连，那就需要在计划里明确写出为什么不复用 Exp.run()。
- 把所有 spawn_fn 测试替身统一迁到 AsyncMock，包括遗漏的测试点。
- 增加一个事件循环可调度性测试，证明没有残留同步 bridge。

**Risk Assessment**: MEDIUM-HIGH

---

## Consensus Summary

### Agreed Strengths
- 架构方向正确：async 迁移遵循 Phase 12-17 的既有模式，bridge 策略合理
- 依赖顺序清晰：Wave 1 (Exp core) -> Wave 2 (spawn) 拆分合理
- Protocol hard cut 一致性：不留 sync/async 双接口

### Key Concerns (Priority Order)

1. **Async cleanup 在同步 bridge 中的完整覆盖** (HIGH) — 所有同步调用点（agent_run_service, DevShell）都必须在 bridge loop 中覆盖 cleanup，否则 async cleanup 不会执行
2. **SpawnTool.execute() 必须保留现有行为契约** (HIGH) — recursion guard、参数校验、异常捕获 + Error: 返回语义不可丢失
3. **Exp.run() 的 try/finally 范围** (HIGH) — 需从 build_runtime() 之前开始，覆盖 partial build failure 的资源清理
4. **测试影响面低估** (MEDIUM) — 遗漏了 test_repl.py、test_runtime.py、test_guard_injection.py、test_compaction_via_devshell.py 等文件
5. **spawn_fn 是否应复用 Exp.run()** (MEDIUM) — 计划文本与 success criteria 存在层次不一致

### Actionable Items

To incorporate this feedback into planning:
```
/gsd:plan-phase 18 --reviews
```
