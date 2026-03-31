---
phase: 19
reviewers: [codex]
reviewed_at: 2026-03-29T23:45:00+08:00
plans_reviewed: [19-01-PLAN.md, 19-02-PLAN.md]
---

# Cross-AI Plan Review -- Phase 19

## Codex Review

### Plan 19-01: Service Layer Bridge Unification + DevShell Simplification

#### 1. Summary
计划的主方向正确，与当前代码真实形态高度吻合：`agent_run_service` 现在确实是 Router 一套 loop、Exp/Kernel 一套 loop 的双桥接结构，统一成单 daemon thread + `run_coroutine_threadsafe(...).result()` 会明显降低复杂度，`DevRunner` 改成 `asyncio.run()` 也属于合理简化。问题主要不在设计方向，而在验证策略偏弱：这次改动会穿过 Router、MessageBus、Bohrium cleanup、quota 扣费和异常收尾路径，如果只写成 删除旧字段 + 现有测试通过，风险控制还不够。

#### 2. Strengths
- 计划准确击中了当前双 loop 的真实分裂点
- 统一使用 `run_coroutine_threadsafe(...).result()` 与现有 `router.start()` 的桥接方式一致，属于延续而不是引入全新机制
- 保持 quota 路径继续走 FastAPI 的事件循环，而不是误绑到 agent loop，这一点非常关键
- `DevRunner` 从手动 `new_event_loop + run_until_complete` 收敛到 `asyncio.run()`，符合它本身作为同步 CLI 入口的职责边界

#### 3. Concerns
- **HIGH**: 计划没有把统一 loop 后最关键的生命周期回归测试写成显式任务。当前服务层有很多集成测试基座，但计划只写"现有测试通过"，不足以证明没有 loop/thread 泄漏、没有 cleanup 丢失、没有事件 drain 回归。这个相位的 blast radius 很大，不应该只靠删除后跑全量测试兜底。
- **MEDIUM**: 统一 loop 会改变 MessageBus 和 EventRouter 的时序关系。计划没有明确验证最终 ResponseEvent、StreamClosedEvent 以及 cleanup 阶段 Bohrium 事件在 router.stop() 前一定被 drain 完。
- **MEDIUM**: cleanup 方案写了顺序，但没有覆盖部分初始化失败的分支。例如 router.start() 失败、build_runtime() 抛错、kernel.run() 抛错之后，daemon loop 是否都能被安全停止、router.stop() 是否幂等、exp._run_cleanup_callbacks() 是否仍能执行。
- **LOW**: D-03 提到 ConfirmationHook.set_loop() 注入，但当前代码里的 ConfirmationHook 并没有这个接口，计划里有一小部分前提来自预期状态而不是当前代码。

#### 4. Suggestions
- 把服务桥接的验证补成显式任务，至少加三类回归测试：
  1. 成功路径下最终 ResponseEvent、run_result、stream_closed 仍按预期到达
  2. build_runtime() 或 kernel.run() 抛异常时，exp cleanup、router.stop()、loop stop 仍发生
  3. Bohrium cleanup 期间发出的事件仍能在 router 关闭前被消费
- 不要把 5+ run_coroutine_threadsafe call sites 当成核心验收标准。更好的验收标准是哪些语义操作必须走统一 loop
- 在计划里明确一个桥接 helper，例如 `_submit(coro)` 和 `_shutdown_loop()`，这样实现时不容易在异常路径里漏掉 stop/join/result(timeout)
- DevShell 侧建议把现有的历史累积和 cleanup 回归测试列为必跑集
- 补一个线程退出断言，确认 run_agent_sync() 返回后不会遗留 agent loop thread

#### 5. Risk Assessment
**MEDIUM** -- 架构方向清晰、实现预计以删除旧复杂度为主，技术方案本身并不花哨；但它碰的是服务层最核心的桥接和收尾逻辑，当前计划缺少专门的生命周期与异常路径测试，所以风险主要来自验证不足，而不是方案本身错误。

---

### Plan 19-02: Parallel Tool Dispatch in AgentKernel

#### 1. Summary
总体思路成立：把 guard 和 pre_tool_call 保持串行，把真正的工具执行并发化，再按原始顺序回填结果。但最大的问题不是实现，而是测试与语义细节还不够严密。特别是"并发确实发生"、"混合 blocked/skipped/executed 时的消息顺序"、以及"现有测试 helper 不支持多 tool_call"，这三点如果不先修正，最后很可能出现测试绿了但 transcript 仍有 bug 的情况。

#### 2. Strengths
- 三阶段拆分很贴合需求，尤其是把 guard 和 pre_tool_call 留在串行阶段，符合 D-06
- 用 asyncio.gather 保证批准执行的工具在同一轮并发，是对 TOOL-06 的直接回应，没有额外过度设计
- 计划已经考虑了异常场景、顺序保持和单工具回归
- 保持 post_tool_call 在工具执行之后触发，语义正确

#### 3. Concerns
- **HIGH**: 性能测试不能证明并发。3 个工具各 sleep(0.1)，总耗时 < 0.5s，串行实现大约 0.3s 也会通过，所以这个测试即使不改代码也可能是绿的。RED 阶段就不可靠。
- **HIGH**: 计划没有明确 blocked/skipped 与 executed 混合时如何保持原始 tool_call 顺序。Phase 1 直接 append blocked/skipped 消息，Phase 3 再 append gather 结果，会把 allowed-blocked-allowed 变成 blocked-allowed-allowed，破坏 transcript 顺序。
- **HIGH**: 现有多工具测试 helper 不能直接复用。ToolCallingProvider 对每个 delta 都写死了 index: 0。直接拿它写多工具测试，_do_stream_llm() 会把多个 tool_call 拼成同一个 call，测试会失真。
- **MEDIUM**: 计划测试只覆盖了 guard 被排除，没有覆盖 mixed SKIP case。不足以说明并发批次里 skip 工具不会进入 gather。
- **MEDIUM**: _execute_tool closure catches exceptions 和 asyncio.gather(return_exceptions=True) 两套策略有重叠。如果 closure 已经吞掉所有异常并转成 ToolResult(error)，那 return_exceptions=True 的价值就只剩很少一部分。建议选一种。
- **MEDIUM**: post_tool_call 的时机会变化。Phase 3 等所有 gather 完再统一跑 post hook，先完成的快工具也要等到最慢工具完成后才会触发 post hook。可能影响 ToolResultEvent 时延。
- **LOW**: 全部工具一律并发执行包括有副作用工具，是明确的产品决策；但计划里最好把这个风险写成 accepted risk。

#### 4. Suggestions
- 把并发测试改成真正能区分串行与并发的形式。3 个工具各 sleep(0.2)，断言总耗时 < 0.35s；或者记录开始时间，断言多个工具有明显重叠区间
- 不要只维护 approved_tools 列表。更稳的做法是维护一个与 response.tool_calls 等长的 outcome 列表，每个位置记录 blocked、skipped、executed-result。最后统一按原索引追加 ToolMessage，这样混合状态不会乱序
- 先修测试基座再写 RED。要么修改 ToolCallingProvider 让每个 tool_call 使用不同 index，要么在并发测试里直接构造 StreamingProvider 的多 delta chunk
- 新增一个混合批次测试，至少覆盖 allowed + blocked + skipped + error + success，断言 ToolMessage 顺序与原始 tool_calls 一致
- 明确异常转换策略。建议去掉 closure 内的 try/except，让 gather 阶段允许 task 抛异常，Phase 3 检查 result 是否为异常对象再统一转成 ToolResult(status="error")
- 如果接受 post hook 批量延迟触发，把这个行为写进计划和测试

#### 5. Risk Assessment
**MEDIUM-HIGH** -- 当前计划对正确性的两个关键面没有钉牢：第一，测试目前还不能可靠地区分真并发和伪并发；第二，混合状态下的消息顺序规则没有写清。一旦这两点处理不好，就会出现功能看似完成，但 transcript、history repair、前端事件时序悄悄出错的情况。

---

## Consensus Summary

只有 Codex 一个审查者，以下是其核心发现的结构化总结。

### Key Concerns (Priority Order)

1. **[HIGH] 19-02 并发测试不可靠** -- 当前 sleep(0.1) + < 0.5s 的阈值无法区分串行与并行，需要更严格的时间约束或重叠区间断言
2. **[HIGH] 19-02 混合状态消息顺序** -- blocked/skipped 在 Phase 1 append、executed 在 Phase 3 append 会打乱原始 tool_call 顺序。需要统一按原索引回填
3. **[HIGH] 19-02 测试基座缺陷** -- ToolCallingProvider 对所有 delta 使用 index: 0，无法正确模拟多 tool_call 场景
4. **[HIGH] 19-01 验证策略不足** -- 桥接改造只依赖现有测试通过，缺少生命周期回归、异常路径、事件 drain 的专项测试
5. **[MEDIUM] 19-02 异常处理策略重叠** -- closure try/except 与 gather(return_exceptions=True) 双重保护，逻辑冗余
6. **[MEDIUM] 19-02 post_hook 时序变化** -- 从逐工具立即触发变为全部完成后批量触发，可能影响事件时延
7. **[MEDIUM] 19-01 cleanup 异常路径未覆盖** -- 部分初始化失败时 loop/thread 是否安全关闭未写入验收标准

### Agreed Strengths
- 架构方向正确，统一 loop 和并行 dispatch 都是合理演进
- 保持 quota 路径走 FastAPI loop、guard/pre_hook 保持串行等关键决策正确
- 以删除代码为主的改造，复杂度可控

### Actionable Items Before Execution
1. 修复 19-02 并发测试：改用 sleep(0.2) + 总耗时 < 0.35s，或断言时间重叠
2. 解决 19-02 消息顺序：改用 outcome 列表按原索引统一 append
3. 修复测试基座：让 ToolCallingProvider 支持多 tool_call index
4. 为 19-01 补充生命周期回归测试任务
5. 统一 19-02 异常处理策略（选 closure catch 或 return_exceptions，不要两者并用）

---

*Phase: 19-tool-dispatch*
*Reviewed: 2026-03-29*
