# Phase 15: Hook 系统异步化 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-27
**Phase:** 15-hook
**Areas discussed:** ConfirmationHook 异步等待方案, run_* helpers 桥接策略, bus.emit() 过渡策略, ReplyQueueLike 双协议统一

---

## ConfirmationHook 异步等待方案

### Q1: ConfirmationHook async 等待用哪种方案？

| Option | Description | Selected |
|--------|-------------|----------|
| asyncio.Future + call_soon_threadsafe | 每次 pre_tool_call 创建 Future，await wait_for(future, timeout)。外部线程用 loop.call_soon_threadsafe(future.set_result, reply) 推送。精确匹配一次性等待语义，无额外依赖。 | ✓ |
| asyncio.Queue + wait_for | asyncio.Queue 替代 queue.Queue。语义更通用但对一次性 request-reply 过度设计。 | |
| janus 双面队列 | sync/async 双端队列，无需 call_soon_threadsafe。但引入新依赖，REQUIREMENTS 已列为 v2.1。 | |

**User's choice:** asyncio.Future + call_soon_threadsafe
**Notes:** 用户先确认了 ConfirmationHook 需要"等待"（拦截型 hook 必须返回 HookAction），理解 async 等待是协程挂起而非线程阻塞。

### Q2: loop 引用怎么传入？

| Option | Description | Selected |
|--------|-------------|----------|
| 构造时注入 | ConfirmationHook.__init__(loop=...)，Exp.assemble() 组装时传入 | ✓ |
| asyncio.get_running_loop() 自动获取 | pre_tool_call 执行时获取当前 loop。无需构造时传入。 | |

**User's choice:** 构造时注入
**Notes:** 与 Kernel 的 _bridge_loop 一致的语义。

---

## run_* helpers 桥接策略

| Option | Description | Selected |
|--------|-------------|----------|
| helpers 变 async + Kernel 桥接 | run_* 全部改 async def，Kernel 用 _sync_call_async 桥接。Phase 17 直接 await。 | ✓ |
| helpers 保持 sync + 内部桥接 | helpers 保持 sync def，内部 loop.run_until_complete。需接收 loop 参数，Phase 17 还要改一次。 | |

**User's choice:** helpers 变 async + Kernel 桥接
**Notes:** 与 Phase 13/14 一致的过渡模式。

---

## bus.emit() 过渡策略

| Option | Description | Selected |
|--------|-------------|----------|
| 保持 sync 调用 | hook 方法改 async def，内部 bus.emit() 不变。Phase 16 改 bus 时再统一 await。 | ✓ |
| 预设 await bus.emit() | 直接写 await，给 MessageBus.emit() 加 async 壳。但 Phase 15 就要动 MessageBus 接口。 | |

**User's choice:** 保持 sync 调用
**Notes:** async def 内调 sync 函数完全合法。bus.emit() 只是 queue.Queue.put()，几乎不阻塞。

---

## ReplyQueueLike 双协议统一

### Q1: src/ 层的 sync ReplyQueueLike 怎么处理？

| Option | Description | Selected |
|--------|-------------|----------|
| 废弃 ReplyQueueLike，改用 loop 注入 | ConfirmationHook 暴露 resolve()/cancel()。src/ 层调 hook.resolve() 代替 reply_queue.put_content()。 | ✓ |
| hooks 版保留，src/ 版延后 | 保留双协议，Phase 19 统一。ConfirmationHook 内部还是调 await reply_queue.get()。 | |

**User's choice:** 废弃 ReplyQueueLike，改用 loop 注入

### Q2: src/ 层调用点改动归哪个 Phase？

| Option | Description | Selected |
|--------|-------------|----------|
| Phase 15 一并改 | 改动很小（put_content → resolve，put_cancel → cancel），不算服务层重构 | ✓ |
| 延后到 Phase 19 | Phase 15 只改 matmaster/ 层，src/ 层用适配器临时包装 | |

**User's choice:** Phase 15 一并改
**Notes:** 改动量极小，不涉及服务层重构。

---

## Claude's Discretion

- ConfirmationHook _pending_future 状态管理细节
- asyncio.TimeoutError → HookAction.SKIP 映射的错误消息
- run_* helpers async 短路逻辑实现方式
- 测试迁移范围和 async mock 策略
- BaseHook 默认实现保持不变（已是 async def）

## Deferred Ideas

None — discussion stayed within phase scope
