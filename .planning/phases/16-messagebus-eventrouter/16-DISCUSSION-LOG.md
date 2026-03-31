# Phase 16: MessageBus + EventRouter 异步化 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-03-28
**Phase:** 16-messagebus-eventrouter
**Areas discussed:** emit() sync 兼容层, EventRouter 与 service 层衔接, emit 调用者迁移范围, Handler 同步 I/O 策略

---

## emit() sync 兼容层

### Q1: MessageBus 需要保留 sync 兼容接口吗？

| Option | Description | Selected |
|--------|-------------|----------|
| 纯 async，不留兼容 | emit() 只提供 async def。DevShell 不在 v2.0 范围。与 Protocol hard cut 一致。 | ✓ |
| 双接口: emit() + emit_nowait() | async emit() + sync emit_nowait()。兼容 DevShell 等 sync 调用者。 | |
| Claude 决定 | 根据实际实现情况决定。 | |

**User's choice:** 纯 async，不留兼容
**Notes:** None

### Q2: emit() 底层用 await queue.put() 还是 put_nowait()？

| Option | Description | Selected |
|--------|-------------|----------|
| put_nowait() | emit 永不阻塞。maxsize=0 时永不抛 QueueFull。签名仍 async def 满足 Protocol。 | ✓ |
| await queue.put() | 真正的 async put。有界队列时提供 backpressure。当前 maxsize=0 与 put_nowait 无差别。 | |
| Claude 决定 | 根据队列配置和性能考量决定。 | |

**User's choice:** put_nowait()
**Notes:** None

### Q3: get() 的 timeout 语义怎么映射？

| Option | Description | Selected |
|--------|-------------|----------|
| asyncio.wait_for 包装 | async get(timeout) 内部 asyncio.wait_for(queue.get(), timeout)。超时抛 TimeoutError。 | ✓ |
| 直接 await queue.get() | 不带 timeout，依赖 asyncio.Event/sentinel 控制退出。更干净但实现复杂。 | |
| Claude 决定 | 根据 EventRouter consume loop 实际需求决定。 | |

**User's choice:** asyncio.wait_for 包装
**Notes:** None

---

## EventRouter 与 service 层衔接

### Q4: EventRouter start()/stop() 对外暴露什么接口？

| Option | Description | Selected |
|--------|-------------|----------|
| async start/stop | 都是 async def。service 层用 _sync_call_async 桥接。Phase 17 后直接 await。 | ✓ |
| start 接受 loop 参数 | start(loop) 内部 loop.create_task()。stop() 保持 sync。API 不干净。 | |
| Claude 决定 | 根据 service 层实际调用模式决定。 | |

**User's choice:** async start/stop
**Notes:** None

### Q5: EventRouter 的 graceful stop 机制怎么实现？

| Option | Description | Selected |
|--------|-------------|----------|
| sentinel event | 定义 _STOP_SENTINEL，stop() 往 bus emit sentinel。consume loop 收到后退出。 | |
| asyncio.Event 信号 | asyncio.Event 替代 threading.Event。consume loop 用 asyncio.wait 等待任一完成。 | ✓ |
| task.cancel() | 直接 cancel task，捕获 CancelledError 后 drain。可能在 handler.handle() 中间触发。 | |
| Claude 决定 | 根据实现复杂度和可靠性决定。 | |

**User's choice:** asyncio.Event 信号
**Notes:** 用户选择 asyncio.Event 而非推荐的 sentinel event，偏好标准异步信号原语。

### Q6: service 层 agent_run_service.py 的改动边界？

| Option | Description | Selected |
|--------|-------------|----------|
| 最小桥接 | router.start()/stop() 改 _sync_call_async 桥接。构造函数和 handler 创建不变。 | ✓ |
| Phase 16 不改 service 层 | EventRouter 自己管理 event loop。但无法与 Kernel loop 共享，不满足成功标准 #4。 | |
| Claude 决定 | 根据成功标准和可行性决定。 | |

**User's choice:** 最小桥接
**Notes:** None

---

## emit 调用者迁移范围

### Q7: 12 个 bus.emit() 调用点的迁移在哪个阶段做？

| Option | Description | Selected |
|--------|-------------|----------|
| Phase 16 一起改 | 所有 bus.emit(event) 改为 await bus.emit(event)。机械性改动，风险极低。 | ✓ |
| 只改 bus/router/handler | emit 调用留给 Phase 17。但 emit 是 async def 却不 await 会产生 coroutine-never-awaited 警告。 | |
| Claude 决定 | 根据影响范围和风险决定。 | |

**User's choice:** Phase 16 一起改
**Notes:** None

---

## Handler 同步 I/O 策略

### Q8: PersistenceHandler 的 DB I/O 怎么处理？

| Option | Description | Selected |
|--------|-------------|----------|
| to_thread 包装 | await asyncio.to_thread(events_table.add_event, ...)。与 Phase 14 模式一致。 | ✓ |
| 直接同步调用 | 合法但阻塞 event loop。DB 写入通常很快但难以保证。不满足全链路无阻塞。 | |
| Claude 决定 | 根据 DB 调用耗时和阻塞风险决定。 | |

**User's choice:** to_thread 包装
**Notes:** None

### Q9: SSEHandler 改 async 后怎么简化？

| Option | Description | Selected |
|--------|-------------|----------|
| 统一 await send_cb | 直接 await self._send_cb(payload)。删除 run_coroutine_threadsafe 和 loop 参数。 | ✓ |
| 保留 async/sync 双路径 | 继续兼容 Worker 模式（sync send_cb）。增加复杂度。 | |
| Claude 决定 | 根据 Worker 模式实际需求决定。 | |

**User's choice:** 统一 await send_cb
**Notes:** None

---

## Claude's Discretion

- EventRouter consume loop 的 asyncio.wait 实现细节
- drain 循环边界和 timeout
- _close_handlers 是否需要改 async
- 测试迁移范围
- WorkspaceHandler 是否一并改造

## Deferred Ideas

None
