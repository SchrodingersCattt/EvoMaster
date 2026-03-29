---
phase: 17
reviewers: [codex]
reviewed_at: 2026-03-29T12:00:00Z
plans_reviewed: [17-01-PLAN.md, 17-02-PLAN.md]
---

# Cross-AI Plan Review — Phase 17

## Codex Review

**总体判断**
这两份计划的主方向是对的：`Plan 17-01` 处理 Kernel 本体异步化，`Plan 17-02` 处理同步入口的过渡桥接，拆分思路也基本符合 Phase 17 的目标。但从当前代码来看，这次不是普通重构，而是破坏性接口迁移。最大的风险不在算法复杂度，而在调用面遗漏和中间态不可运行：如果 `AgentKernel.run()` 先改成 `async def`，而所有同步入口没有同一波次补桥，仓库会立刻进入半失效状态。安全层面没有明显新增攻击面，主要风险集中在事件循环边界、provider 生命周期和测试覆盖漏网点。

### Plan 17-01: AgentKernel async化

**Summary**
这份计划对 Kernel 内部改造点抓得比较准，特别是 `run/_run_loop/_call_llm/_do_stream_llm` 整体 async 化、`async for` 消费流式响应、`asyncio.sleep` 替换阻塞 sleep，这些都直接命中 KERN-01/02/03/06。问题在于它把自己当成了一个可以独立完成的 Wave，但从当前仓库结构看，这个 Wave 单独落地并不自洽，而且计划里混入了至少一处过时设计假设。

**Strengths**
- 明确删除 Kernel 内部 bridge，符合 D-04，能把异步边界从内核层移走。
- 对流式路径抓得准确，`_do_stream_llm` 改成 `async for` 是这次迁移最关键的叶节点之一。
- 保留 `threading.Event` 作为 `stop_event` 是合理的，兼容跨线程取消语义，避免把取消机制一起重构导致范围失控。
- `summary_provider` 与主 provider 分实例时单独管理上下文，这个意识是对的，能避免 compaction provider 泄漏。

**Concerns**
- `HIGH`: 这个 Wave 单独不可合并。当前同步调用方仍大量直接依赖同步 `kernel.run()`，包括 exp.py、runner.py、agent_run_service.py。如果 17-01 先落地而 17-02 未完成，主链路会直接拿到 coroutine。
- `MEDIUM`: `ConfirmationHook: asyncio.get_running_loop() for set_loop()` 这一条很像过时方案。当前 hook 协议要求 async 方法，但现有 confirmation.py 既没有 `set_loop()`，`pre_tool_call` 还是同步实现。也就是说，计划现在瞄准的不是代码里的真实不一致点。
- `MEDIUM`: provider 生命周期虽然有实现思路，但计划没有把它变成显式回归测试。尤其是主 provider 和 summary provider 同实例、不同实例、以及 `__aenter__/__aexit__` 异常路径，这些都值得单测覆盖。
- `LOW`: 把 `test_agent.py` 里 35 个测试全部改成 async 有点偏机械。真正需要 async 的是调用 `run()` / `_call_llm()` 的测试；纯静态方法或纯数据测试继续保持同步可减少噪音和 diff 面积。

**Suggestions**
- 把 `17-01 + 17-02` 视为一个原子交付单元，而不是可独立合并的两个波次。
- 把 `ConfirmationHook` 从计划里单独澄清：如果当前阶段仍不接入它，就明确排除；如果要覆盖它，真正要修的是 async hook 兼容性，而不是 `set_loop()`。
- 为 provider 生命周期补三类回归测试：同实例 summary provider、独立实例 summary provider、进入或退出上下文抛异常时的清理顺序。
- 在计划里明确说明 `EventEmitterHook` 此阶段仍保留 `emit_nowait()`，避免有人顺手把总线调用也改成 `await bus.emit()`，引入跨线程队列问题。

**Risk Assessment:** HIGH
理由：Kernel 内部改造本身思路正确，但它是破坏性签名变更，且计划里存在过时设计假设；如果不与所有同步入口一起落地，仓库会立刻进入不可运行中间态。

### Plan 17-02: Exp/spawn/DevRunner bridge + external tests

**Summary**
这份计划的桥接策略总体合理，尤其是把 bridge 从 `agent.py` 移到同步入口，符合 D-02 和 D-05；但它目前明显低估了真实调用面。按当前代码看，计划覆盖了 `Exp.run`、`spawn_fn`、`DevRunner.run`，却漏掉了生产主链路 `AgentRunService.run_agent_sync`，同时对测试迁移范围和 `AsyncMock` 需求都估得偏小，所以它还不足以保证 Phase 17 达到可交付状态。

**Strengths**
- 过渡桥接放在同步边界而不是放回 Kernel，架构方向是干净的。
- 把 `spawn_fn` 纳入同一波次是对的，因为它是最容易被漏掉的隐式同步入口。
- 注意到 `test_exp.py` 里的 `mock_kernel.run` 需要 `AsyncMock`，说明计划意识到了 awaitable mock 的问题。
- 仓库已经启用 `pytest-asyncio` 的 `auto` 模式，测试 async 化的基础设施是现成的，这降低了迁移摩擦。

**Concerns**
- `HIGH`: 生产路径遗漏。当前真正的业务执行入口之一 agent_run_service.py 直接调用 `runtime.kernel.run(...)`，而它背后连着 API/Worker 主链路。Plan 17-02 不包含这里，意味着 Phase 17 即使"代码通过局部测试"，生产入口仍会坏。
- `HIGH`: 测试范围明显低估。直接 `kernel.run()` 调用至少散落在 10 个测试文件，不只是 `test_agent.py` 和 8 个外部文件。
- `HIGH`: `AsyncMock` 需求也被低估了，不只是 `test_exp.py`。例如 test_subagent_spawn.py 大量用 `MagicMock().run.return_value/side_effect` 模拟 child kernel；一旦 `spawn_fn` 用 `run_until_complete` 桥接，这些 mock 全都会失效。
- `MEDIUM`: `spawn_fn` 复用父 `ctx`，也就复用了父 `llm_provider`。如果父子 run 都对同一个 provider 实例做 `async with`，子 run 退出时可能把父 run 还要继续用的 client 提前关掉。当前计划没有任何回归测试覆盖这个风险。
- `MEDIUM`: `loop.close() BEFORE runtime.cleanup()` 被标成 critical，但计划没有给出行为依据，也没有测试去锁定这个顺序。
- `LOW`: 某些测试里的 provider double 仍是同步风格。这类夹杂的 sync stub 不清理干净，最终会把失败原因变得很噪。

**Suggestions**
- 把 `src/services/agent_run_service.py` 及其相关集成测试纳入 Wave 2 的必选范围，而不是留到后续阶段。
- 不要再用 8 个/9 个文件这种静态估算，改成 grep 驱动的完成定义：扫完所有 `kernel.run(`、`mock_kernel.run`、`spawn_fn`、`AsyncMock` 相关调用后再宣称收口。
- 为 `spawn` 补一个真正的嵌套回归测试，验证父 run 继续执行时 provider 生命周期没有被 child run 破坏。
- 把内联 bridge 片段写成明确模板并在每个同步入口逐字复用，至少覆盖 `Exp.run`、`spawn_fn`、`DevRunner.run`、`AgentRunService.run_agent_sync` 四处，避免 4 份近似代码产生微差。
- 在计划里注明本阶段的性能预期：同步入口仍是一请求一事件循环，性能收益主要来自内核内部不再阻塞，而不是端到端并发模型升级。

**Risk Assessment:** HIGH
理由：桥接方向对，但当前计划遗漏了生产主入口，并明显低估了测试与 mock 改造面；如果按现有范围执行，Phase 17 很可能会得到一个局部可跑、整体不可交付的结果。

---

## Consensus Summary

(Single reviewer — Codex only. Consensus represents Codex's analysis.)

### Key Concerns (by severity)

**HIGH:**
1. **生产路径遗漏** — `agent_run_service.py` 直接调用 `kernel.run()`，不在任何 plan 范围内。Phase 17 落地后生产入口会坏。
2. **Wave 1 单独不可运行** — 17-01 改 kernel 签名后，所有同步调用方拿到 coroutine 而非结果。两个 wave 必须原子交付。
3. **AsyncMock 低估** — test_subagent_spawn.py 等文件的 mock kernel 也需要 AsyncMock 适配。
4. **测试范围低估** — 静态列出的文件数不够，应 grep 驱动确认完整调用面。

**MEDIUM:**
1. **ConfirmationHook 过时假设** — 计划引用的 set_loop() 可能与当前代码不一致，需验证。
2. **Provider 生命周期风险** — spawn_fn 复用父 provider，子 run async with 退出可能关闭父 provider。缺回归测试。
3. **loop.close() 顺序无测试** — 标为 critical 但无行为依据和锁定测试。

**LOW:**
1. 部分同步 stub 测试未清理，增加噪音。
2. 35 个测试全量 async 化偏机械，纯数据测试可保持同步。

### Recommendations
1. 将 `agent_run_service.py` 桥接纳入 Plan 17-02 范围
2. 两个 wave 视为原子交付单元
3. 用 grep 驱动确认完整调用面，不依赖静态文件列表
4. 补 provider 生命周期回归测试（同实例/异实例/异常路径）
5. 验证 ConfirmationHook 当前实现与计划假设是否一致
