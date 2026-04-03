# Phase 36: 去总线化 + 高级调度 - Context

**Gathered:** 2026-04-03
**Status:** Ready for planning

<domain>
## Phase Boundary

MessageBus + EventRouter 物理删除。Service 层事件分发改为 async fanout 直连 handler（SSE 同步优先 + 持久化异步）。ConfirmationHook 删除（后续 generator 双向流重建）。run_agent() 旧路径删除，run_agent_stream() 改造后更名为 run_agent()。DevShell 事件路径独立改造。ASCH-01（高级调度）跳过——当前无 persistent shell 消费场景。

</domain>

<decisions>
## Implementation Decisions

### 去总线化范围
- **D-01:** MessageBus（bus.py）和 EventRouter（event_router.py）物理删除。不降级保留、不标记 deprecated。一次性完成。
- **D-02:** 所有 bus.emit / bus.emit_nowait 调用点改为 fanout 直连 handler。包括 run_agent_stream() 主循环和后处理事件（CancelledEvent / StreamClosedEvent）。

### ConfirmationHook 处置
- **D-03:** ConfirmationHook 直接删除。不改造为 event_sink 模式。当前确认功能前端未使用，后续在 generator 双向流设计中重建（v2.3+）。
- **D-04:** 随 ConfirmationHook 删除，清除 `matmaster/hooks/` 目录（仅剩 confirmation.py）。Hook 基础设施（Hook Protocol / BaseHook / HookAction）的清理程度由 Claude 根据 DevStreamHook 和剩余消费者的实际依赖决定。

### async fanout 设计
- **D-05:** SSE 同步 await 优先 + 持久化 asyncio.create_task() 异步。SSEHandler.handle() 在事件循环中同步执行保证前端低延迟，PersistenceHandler.handle() 通过 create_task 异步执行不阻塞事件流。
- **D-06:** 持久化 task 通过 TaskGroup（或 set[Task]）收集管理生命周期。run 结束时 drain 剩余 task（类似当前 EventRouter.stop() 的 drain 逻辑）。不丢事件。

### run_agent() 旧路径
- **D-07:** 删除 run_agent() 方法。上游调用者（`src/worker/agent_worker.py` L254）迁移到 run_agent_stream()。
- **D-08:** 改造完成后 run_agent_stream() 更名为 run_agent()。最终 AgentRunService 只有一个执行入口。
- **D-09:** Worker 模式（无 SSE）的 send_cb 处理方式由 Claude 决定（no-op callback 或可选 handler 列表）。

### ASCH-01 高级调度
- **D-10:** Phase 36 跳过 ASCH-01。当前 LocalSession 和 SSHSession 均为 shell_persistence="stateless"，无 persistent shell 消费场景。待未来 persistent shell 实现后再追加调度增强。

### Claude's Discretion
- Hook 基础设施（Hook Protocol / BaseHook / HookAction / dispatch 函数）的清理范围——取决于 DevStreamHook 和 InlineToolRunner 的实际依赖
- fanout 函数的具体放置位置（新文件 vs 内联 vs integration 模块）
- Worker 模式的 SSE 处理（no-op send_cb vs handlers 列表可选）
- DevShell 路径改造的具体方式（EventLogger 本身不是 EventHandler，可能只需删除 MessageBus 导入改为直接调用）
- run_agent() 删除后 agent_run_bohrium.py 中日志字符串的清理
- InlineToolRunner 是否同步删除（FullToolRunner 已是默认路径）

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 架构 Spec
- `docs/specs/2026-04-02-kernel-generator-first.md` §10 Phase 3 (L1881-1885) — 去总线化评估：是否移除 Bus + Router、async fanout + buffer 替代、SSE 先发/持久化不阻塞语义
- `docs/specs/2026-04-02-tool-runtime-v2.md` §13 Phase 3 (L979-988) — 高级调度可选方向（persistent shell 并发 / web_fetch 上限 / spawn 限并发 / SessionCapabilities 自适应）。Phase 36 跳过此部分。

### 推进设计
- `docs/plans/2026-04-02-v2.2-phase2-advancement.md` — v2.2 Phase 2 三波次推进设计。Phase 36 是 Wave A-C 之后的收尾阶段。背景参考：Wave B 的 generator 切流架构 + Wave C 的约束迁移

### 改造目标文件
- `src/services/agent_run_service.py` — run_agent() 删除 + run_agent_stream() 去总线化改造 + 更名
- `matmaster/core/bus.py` — MessageBus 删除
- `matmaster/integration/event_router.py` — EventRouter 删除
- `matmaster/hooks/confirmation.py` — ConfirmationHook 删除
- `matmaster/integration/sse_handler.py` — SSEHandler（保留，fanout 直连）
- `matmaster/integration/persistence_handler.py` — PersistenceHandler（保留，fanout 异步调用）
- `src/worker/agent_worker.py` L254 — run_agent() 调用迁移
- `matmaster/devshell/debug_run.py` / `cli.py` / `repl.py` / `runner.py` — DevShell MessageBus 使用清理

### Phase 34-35 产出
- `matmaster/core/agent.py` — AgentKernel._run_items() / run_stream()（generator 事件源）
- `matmaster/core/exp.py` — Exp.run_stream()（透传 generator）
- `matmaster/core/context_compactor.py` — 已改为 event_sink 模式（Phase 34 D-07 参考）

### 需求定义
- `.planning/REQUIREMENTS.md` — DBUS-01~03（去总线化）、ASCH-01（跳过）

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `matmaster/integration/event_router.py` L99-111: EventRouter._dispatch() 的错误隔离模式（per-handler try/except + logging）可直接复用到 fanout 函数
- `matmaster/integration/event_router.py` L65-88: EventRouter.stop() 的 drain 逻辑是 TaskGroup drain 的参考模板
- `matmaster/core/context_compactor.py` L150-151: event_sink 回调模式（Phase 34 D-07）是 ConfirmationHook 改造的参考（但本次直接删除）
- `matmaster/integration/sse_handler.py`: SSEHandler.handle() 纯 async，可直接被 fanout 调用
- `matmaster/integration/persistence_handler.py`: PersistenceHandler.handle() 纯 async，可直接被 create_task 包装

### Established Patterns
- EventHandler Protocol（event_router.py L28-33）: `async def handle(event: BusEvent) -> None` — fanout 可复用此 Protocol
- handler 注册顺序决定优先级（SSEHandler 排第一）
- handler 的 close() 方法用于资源清理（event_router.py L113-132）

### Integration Points
- run_agent_stream() L536-554: EventRouter 创建点（改为 fanout 构造）
- run_agent_stream() L666-673: generator 事件消费循环 → bus.emit_nowait（改为 fanout 调用）
- run_agent_stream() L684-704: 后处理事件 → bus.emit_nowait（改为 fanout 调用）
- agent_worker.py L254: run_agent() 唯一外部调用者
- DevShell 4 个文件独立使用 MessageBus（与 Service 层无关联）

</code_context>

<specifics>
## Specific Ideas

- EventRouter._dispatch() 的 per-handler try/except 模式直接搬到 fanout 函数，保持错误隔离语义一致
- fanout 结束时的 drain 逻辑参考 EventRouter.stop()，但改为 await TaskGroup 而非 bus.get_nowait()
- DevShell 的 EventLogger 使用 log_event() 而非 handle()，不是 EventHandler Protocol 实现。DevShell 路径清理可以简单化——直接删除 MessageBus 使用，让 runner/repl 直接调用 EventLogger.log_event()
- run_agent_stream() 更名为 run_agent() 后，src/services/stream_service.py 中的 docstring 引用需同步更新
- ContextCompactor 的 event_sink 在 Phase 34 已改造完毕，不受 Bus 删除影响（只要 sink 回调正确注入）

</specifics>

<deferred>
## Deferred Ideas

- ASCH-01 高级调度 — 待 persistent shell 实现后追加
- ConfirmationHook generator 双向流重建 — v2.3+
- InlineToolRunner 清理 — 如果 Phase 36 未删除，留给后续

</deferred>

---

*Phase: 36-debus-scheduling*
*Context gathered: 2026-04-03*
