# Phase 17: AgentKernel 异步化 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-03-28
**Phase:** 17-agentkernel
**Areas discussed:** run() 返回模型, Exp→Kernel 过渡桥接, Bridge 函数去留

---

## run() 返回模型

| Option | Description | Selected |
|--------|-------------|----------|
| async def → KernelRunResult | run() 变 async，返回类型不变。事件继续走 MessageBus。与 Phase 16 工作衔接，改动量最小。KERN-01 修订为 async def。 | ✓ |
| async generator → yield AgentEvent | 按 KERN-01 原始设计。根本改变事件流转架构，从 bus.emit push 变为 yield pull。与 Phase 16 MessageBus 工作冲突，改动量大。 | |
| 两者兼有 | run() 返回 KernelRunResult，另加 stream() async generator 接口。维护两套事件路径，复杂度高。 | |

**User's choice:** async def → KernelRunResult (推荐)
**Notes:** KERN-01 的 async generator 描述与 Phase 16 已完成的 MessageBus async 架构冲突。保持 KernelRunResult 返回模型，事件走 MessageBus，是最自然的选择。

---

## Exp→Kernel 过渡桥接

| Option | Description | Selected |
|--------|-------------|----------|
| Bridge 移入 Exp | Exp.run() 内创建临时 bridge loop + run_until_complete 调用 async kernel.run()。模式与当前 agent.py 一致，位置上移一层。Phase 18 移除。agent.py 彻底清洁。 | ✓ |
| Bridge 提取到共享模块 | 创建 matmaster/utils/async_bridge.py。Exp/DevShell/测试都可用。多一个文件但更整洁。 | |
| 延伸 Phase 17 覆盖 Exp.run() | Phase 17 顺带把 Exp.run() 也改 async。避免临时桥接，但超出 Phase 17 边界，侵入 Phase 18 范围。 | |

**User's choice:** Bridge 移入 Exp (推荐)
**Notes:** 保持分层迁移纪律。Kernel 彻底 async，bridge 责任上移到调用者（Exp）。Phase 18 自然消除。

---

## Bridge 函数去留

| Option | Description | Selected |
|--------|-------------|----------|
| 彻底删除，Exp 内联桥接 | agent.py 删除所有 bridge 代码。Exp.run() 内联 5 行 asyncio.new_event_loop() + run_until_complete 模式。唯一消费者只有 Exp，不值得抽取共享模块。 | ✓ |
| 提取到共享工具模块 | 创建 matmaster/utils/async_bridge.py。虽然只有 Exp 用，但未来 DevShell 或其他 sync 调用者也可能需要。 | |
| 保留在 agent.py | 保留 bridge 函数但只给外部使用。Kernel 模块包含非 Kernel 工具，职责不清晰。 | |

**User's choice:** 彻底删除，Exp 内联桥接 (推荐)
**Notes:** agent.py 变为纯 async 模块。不为假设性需求创建抽象。

---

## Claude's Discretion

- ConfirmationHook loop 注入：async run() 中用 asyncio.get_running_loop() 替代创建新 loop
- Provider 生命周期：async with 语法替代手动 __aenter__/__aexit__
- 测试迁移范围和 async mock 策略
- GuardPipeline 保持同步调用
- stop_event 保持 threading.Event

## Deferred Ideas

None -- discussion stayed within phase scope
