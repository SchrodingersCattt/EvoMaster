# Phase 16: MessageBus + EventRouter 异步化 - Context

**Gathered:** 2026-03-28
**Status:** Ready for planning

<domain>
## Phase Boundary

将事件传输链路全面 async 化：MessageBus 底层从 queue.Queue 改为 asyncio.Queue，EventRouter 从 threading.Thread 改为 asyncio.Task 消费循环，SSEHandler 和 PersistenceHandler 的 handle() 改为 async def。同时将所有 bus.emit() 调用者（12 个调用点）统一改为 await bus.emit()。

本阶段不改 AgentKernel（Phase 17）、Exp 生命周期（Phase 18）、service 层整体重构（Phase 19）。Service 层 agent_run_service.py 做最小桥接改动（router.start()/stop() 用 _sync_call_async 桥接）。

</domain>

<decisions>
## Implementation Decisions

### MessageBus API 设计
- **D-01:** MessageBus 纯 async，不保留 sync 兼容接口。与 Protocol hard cut 决策一致。DevShell 不在 v2.0 范围，将来自行适配。
- **D-02:** emit() 签名为 async def，底层使用 put_nowait()（不实际 await）。maxsize=0 时 put_nowait 永不抛 QueueFull，避免不必要的协程切换。方法签名满足 async Protocol 要求。
- **D-03:** get() 使用 asyncio.wait_for(queue.get(), timeout) 包装。超时抛 asyncio.TimeoutError。EventRouter consume loop 用 try/except TimeoutError 替代当前 queue.Empty。get_nowait() 保留为同步方法（drain 场景使用）。

### EventRouter 生命周期
- **D-04:** start() 和 stop() 都是 async def。使用 asyncio.create_task 启动消费循环。service 层通过 _sync_call_async(router.start(), bridge_loop) 桥接调用，与 Phase 13-15 模式一致。Phase 17 Kernel async 化后直接 await。
- **D-05:** graceful stop 使用 asyncio.Event 信号。consume loop 用 asyncio.wait 同时等待 queue.get() 和 stop_event.wait()，任一完成则检查。收到 stop 信号后 drain 剩余事件再退出。
- **D-06:** EventRouter 内部 _stop_event 从 threading.Event 改为 asyncio.Event。_thread 字段改为 _task: asyncio.Task。

### Service 层适配
- **D-07:** service 层做最小桥接。agent_run_service.py 中 router.start()/stop() 调用点改为 _sync_call_async 桥接。构造函数签名不变（bus + handlers）。handler 创建逻辑不变。Phase 19 再整体重构 service 层。

### emit 调用者迁移
- **D-08:** Phase 16 一起改所有 12 个 bus.emit() 调用点为 await bus.emit()。改动纯机械性（加 await 关键字）。分布在 4 个 Hook 实现 + EventEmitterHook（6 处）+ ContextCompactor（2 处）。所有调用点已在 async def 方法中（Phase 15 完成），无兼容问题。

### Handler async 化
- **D-09:** PersistenceHandler.handle() 改为 async def，内部 events_table.add_event() 用 asyncio.to_thread 包装。与 Phase 14 BuiltinTool 模式一致，DB 写入不阻塞 event loop。
- **D-10:** SSEHandler.handle() 改为 async def，统一 await self._send_cb(payload)。删除 run_coroutine_threadsafe 路径和 _loop/_is_async 字段。EventRouter 已在 async task 中运行，不需要跨线程调度。

### Claude's Discretion
- EventRouter consume loop 的 asyncio.wait 具体实现方式（wait vs wait_for vs gather + shield）
- drain 逻辑中 get_nowait 的循环边界和 timeout 处理
- _close_handlers 是否需要改 async（handler.close() 是否涉及 I/O）
- 测试迁移范围和 async mock 策略
- WorkspaceHandler 是否需要在本阶段一并改 async handle()

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements & Roadmap
- `.planning/REQUIREMENTS.md` -- INFR-01, INFR-02, INFR-03 requirements 定义
- `.planning/ROADMAP.md` -- Phase 16 目标、依赖、成功标准
- `.planning/PROJECT.md` -- Protocol hard cut, DevShell 延后, 自底向上迁移

### 前置阶段 Context
- `.planning/phases/12-protocol/12-CONTEXT.md` -- EventHandler Protocol async handle() 签名（D-02）
- `.planning/phases/13-llm-provider/13-CONTEXT.md` -- _sync_call_async 桥接模式（D-04），_bridge_loop 机制
- `.planning/phases/14-tool/14-CONTEXT.md` -- asyncio.to_thread 包装策略（D-02），ToolRegistry async execute
- `.planning/phases/15-hook/15-CONTEXT.md` -- Hook async 化完成，bus.emit() sync 过渡约定（D-08）

### MessageBus（改造核心）
- `matmaster/core/bus.py` -- MessageBus 当前实现（50 行，queue.Queue 封装）

### EventRouter + Handlers（改造核心）
- `matmaster/integration/event_router.py` -- EventRouter（130 行，threading.Thread 消费循环）+ EventHandler Protocol
- `matmaster/integration/sse_handler.py` -- SSEHandler（async/sync 双路径 send_cb）
- `matmaster/integration/persistence_handler.py` -- PersistenceHandler（sync DB events_table.add_event）

### emit 调用者（迁移范围）
- `matmaster/core/hooks.py` -- EventEmitterHook（6 个 bus.emit 调用）
- `matmaster/hooks/output_processor.py` -- OutputProcessorHook（2 个 bus.emit）
- `matmaster/hooks/confirmation.py` -- ConfirmationHook（1 个 bus.emit）
- `matmaster/hooks/skill_hit.py` -- SkillHitHook（1 个 bus.emit）
- `matmaster/hooks/assistant_state.py` -- AssistantStateHook（1 个 bus.emit）
- `matmaster/core/context_compactor.py` -- ContextCompactor（2 个 bus.emit）

### Service 层桥接（最小改动）
- `src/services/agent_run_service.py` -- router.start()/stop() 调用点（:282, :301, :545）
- `matmaster/core/agent.py` -- _sync_call_async 桥接函数

### 测试文件
- `tests/matmaster/integration/` -- EventRouter、Handler 测试
- `tests/matmaster/core/test_bus.py` -- MessageBus 测试（如存在）
- `tests/conftest.py` -- Phase 12 建立的 async mock factories

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `_sync_call_async` / `_sync_iterate_async` 桥接函数已在 agent.py 中建立（Phase 13）
- Phase 12 建立的 pytest-asyncio 基础设施 + async mock factories（tests/conftest.py）
- validate_async_protocol() helper 可验证 EventHandler 实现
- Phase 14 建立的 asyncio.to_thread 包装模式

### Established Patterns
- EventHandler Protocol 已声明 async def handle()（Phase 12）
- MessageBus 是 queue.Queue 的薄封装（50 行），改造量极小
- EventRouter 是单消费者模式：一个后台线程，多 handler dispatch
- SSEHandler 已有 async/sync 双路径，改造后简化为纯 async
- PersistenceHandler 纯 sync I/O，需要 to_thread 包装

### Integration Points
- MessageBus 被 Exp.assemble() 创建，传递给 Hook 和 EventRouter
- EventRouter 被 agent_run_service.py 创建、启动、停止
- SSEHandler/PersistenceHandler/WorkspaceHandler 在 agent_run_service.py 中创建并注册到 EventRouter
- DevShell 独立创建 MessageBus（不在 v2.0 范围，暂不改）

</code_context>

<specifics>
## Specific Ideas

- emit() 用 put_nowait 而非 await put() 是因为 maxsize=0（无界队列），两者行为等价但 put_nowait 避免协程切换开销。如果未来引入 backpressure（有界队列），可改为 await put()。
- EventRouter stop 选择 asyncio.Event 而非 sentinel event，因为 asyncio.Event 是标准的异步信号原语，与 asyncio.wait 配合使用更自然。
- SSEHandler 删除 run_coroutine_threadsafe 路径后，构造函数签名简化：不再需要 loop 参数和 _is_async 检测。这是一个净减少代码的改造。
- service 层桥接模式（_sync_call_async）是临时方案，Phase 17-19 逐步移除。

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope

</deferred>

---

*Phase: 16-messagebus-eventrouter*
*Context gathered: 2026-03-28*
