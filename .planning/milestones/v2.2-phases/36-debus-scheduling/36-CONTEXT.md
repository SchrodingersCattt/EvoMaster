# Phase 36: 去总线化 + 高级调度 - Context

**Gathered:** 2026-04-03
**Status:** Ready for planning

<domain>
## Phase Boundary

MessageBus + EventRouter 物理删除。Service 层事件分发改为 async fanout 直连 handler（SSE 同步优先 + 持久化异步）。Bohrium 事件桥接从 bus.emit_nowait 迁移到 fanout 线程安全入口。ConfirmationHook 删除（后续 generator 双向流重建）。run_agent() 旧路径删除，run_agent_stream() 改造后更名为 run_agent()。DevShell 中 MessageBus 依赖替换为轻量替代（asyncio.Queue 或直接调用），但不迁移 DevShell 到 run_stream()（FUTR-03 仍属 v2.3+）。ASCH-01（高级调度）跳过——当前无 persistent shell 消费场景。

</domain>

<decisions>
## Implementation Decisions

### 去总线化范围
- **D-01:** MessageBus（bus.py）和 EventRouter（event_router.py）物理删除。不降级保留、不标记 deprecated。一次性完成。
- **D-02:** 所有 bus.emit / bus.emit_nowait 调用点改为 fanout 直连 handler。包括 run_agent_stream() 主循环、后处理事件（CancelledEvent / StreamClosedEvent）、以及 BohriumSetupService._make_event_bridge() 的线程安全桥接。

### Bohrium 事件桥接（线程安全）
- **D-11:** `agent_run_bohrium.py` L360-414 的 `_make_event_bridge()` 当前通过 `loop.call_soon_threadsafe(bus.emit_nowait)` 从 Bohrium 工作线程推送 ErrorEvent/StreamClosedEvent/BohriumNodeEvent。Bus 删除后此路径必须同步改造。替代方案：`_make_event_bridge()` 接受 fanout 的同步入口（`loop.call_soon_threadsafe` 调度一个将事件送入 fanout 的闭包），或者 BohriumSetupService 接受 `event_sink: Callable` 替代 `bus` 参数。此改造直接关系 ROADMAP 成功标准 3（Kernel 外事件通过 async fanout 直连消费者）。

### ConfirmationHook 处置
- **D-03:** ConfirmationHook 直接删除。不改造为 event_sink 模式。当前确认功能前端未使用，后续在 generator 双向流设计中重建（v2.3+）。
- **D-04:** 随 ConfirmationHook 删除，清除 `matmaster/hooks/` 目录（仅剩 confirmation.py）。Hook 基础设施（Hook Protocol / BaseHook / HookAction）的清理程度由 Claude 根据 DevStreamHook 和剩余消费者的实际依赖决定。

### async fanout 设计
- **D-05:** SSE 同步 await 优先 + 持久化 asyncio.create_task() 异步。SSEHandler.handle() 在事件循环中同步执行保证前端低延迟，PersistenceHandler.handle() 通过 create_task 异步执行不阻塞事件流。
- **D-06:** 持久化 task 通过 TaskGroup（或 set[Task]）收集管理生命周期。run 结束时 drain 剩余 task（类似当前 EventRouter.stop() 的 drain 逻辑）。不丢事件。
- **D-12:** fanout 必须接管 EventRouter.stop() 当前承担的 handler 生命周期管理。具体：run 结束时（正常或异常）先 drain 所有 pending persistence task，然后对所有 handler 调用 close()（如果存在）。WorkspaceHandler.close()（L173-183）依赖此阶段等待挂起的 workspace 上传结束——丢失 close() 调用会导致归档上传静默丢弃。

### run_agent() 旧路径
- **D-07:** 删除 run_agent() 方法。上游调用者（`src/worker/agent_worker.py` L254）迁移到 run_agent_stream()。
- **D-08:** 改造完成后 run_agent_stream() 更名为 run_agent()。最终 AgentRunService 只有一个执行入口。
- **D-09:** Worker 模式（无 SSE）的 send_cb 处理方式由 Claude 决定（no-op callback 或可选 handler 列表）。

### ASCH-01 高级调度
- **D-10:** Phase 36 跳过 ASCH-01。当前 LocalSession 和 SSHSession 均为 shell_persistence="stateless"，无 persistent shell 消费场景。待未来 persistent shell 实现后再追加调度增强。

### DevShell 范围界定
- **D-13:** DevShell（runner.py / cli.py / repl.py / debug_run.py）当前直接使用 MessageBus 作为 runner→EventLogger 的事件通道。Phase 36 删除 MessageBus 后这些代码会破损。改造范围：将 MessageBus 依赖替换为轻量替代（asyncio.Queue 或直接调用 EventLogger.log_event()），保持 DevShell 现有架构不变。这不是 FUTR-03（DevShell 消费 run_stream() 替代 run() + Bus），FUTR-03 仍属 v2.3+ 范围。Phase 36 只做 MessageBus 符号消除，不改变 DevShell 的执行模型。

### Claude's Discretion
- Hook 基础设施（Hook Protocol / BaseHook / HookAction / dispatch 函数）的清理范围——取决于 DevStreamHook 和 InlineToolRunner 的实际依赖
- fanout 函数/类的具体形式和放置位置
- Worker 模式的 SSE 处理（no-op send_cb vs handlers 列表可选）
- DevShell 中 MessageBus 的具体替代实现（asyncio.Queue vs 直调 vs 其他）
- Bohrium 事件桥接的线程安全入口的具体实现（BohriumSetupService 接受 event_sink 还是 fanout 暴露 sync 入口）
- InlineToolRunner 是否同步删除（FullToolRunner 已是默认路径）
- ContextCompactor bus= 兼容参数的清理

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 架构 Spec
- `docs/specs/2026-04-02-kernel-generator-first.md` §10 Phase 3 (L1881-1885) — 去总线化评估：是否移除 Bus + Router、async fanout + buffer 替代、SSE 先发/持久化不阻塞语义
- `docs/specs/2026-04-02-tool-runtime-v2.md` §13 Phase 3 (L979-988) — 高级调度可选方向（persistent shell 并发 / web_fetch 上限 / spawn 限并发 / SessionCapabilities 自适应）。Phase 36 跳过此部分。

### 推进设计
- `docs/plans/2026-04-02-v2.2-phase2-advancement.md` — v2.2 Phase 2 三波次推进设计。Phase 36 是 Wave A-C 之后的收尾阶段。背景参考：Wave B 的 generator 切流架构 + Wave C 的约束迁移

### 改造目标文件（主链路）
- `src/services/agent_run_service.py` — run_agent() 删除 + run_agent_stream() 去总线化改造 + 更名为 run_agent()
- `src/services/agent_run_bohrium.py` L300-416 — BohriumSetupService._bus 字段 + _make_event_bridge() 线程安全桥接改造
- `matmaster/core/bus.py` — MessageBus 物理删除
- `matmaster/integration/event_router.py` — EventRouter 物理删除
- `matmaster/hooks/confirmation.py` — ConfirmationHook 删除
- `matmaster/integration/sse_handler.py` — SSEHandler（保留，fanout 直连）
- `matmaster/integration/persistence_handler.py` — PersistenceHandler（保留，fanout 异步调用）
- `matmaster/integration/workspace_handler.py` L173-183 — close() 生命周期被 fanout owner 接管
- `src/worker/agent_worker.py` L254 — run_agent() 调用迁移

### 改造目标文件（符号消除 + 兼容清理）
- `matmaster/core/__init__.py` L8 — MessageBus 导出删除
- `matmaster/integration/__init__.py` L7 — EventRouter/EventHandler 导出删除
- `matmaster/core/context_compactor.py` L141,149-151 — bus= 兼容参数和 backward compat 分支删除
- `matmaster/core/exp.py` — bus 参数从 run()/run_stream()/build_runtime() 签名清理
- `matmaster/devshell/debug_run.py` / `cli.py` / `repl.py` / `runner.py` — MessageBus 依赖替换为轻量替代
- `src/services/stream_service.py` — docstring 中 run_agent 引用更新

### 测试面影响（blast radius）
- `tests/matmaster/core/test_bus.py` — 整文件删除
- `tests/matmaster/integration/test_event_router.py` — EventRouter 相关用例删除，SSEHandler/PersistenceHandler 回归用例拆分迁移（保留 SSE 过滤规则、持久化过滤规则、sync/async send_cb 分发等关键回归保护）
- `tests/matmaster/services/test_agent_run_stream.py` — bus/router mock 改造
- `tests/matmaster/test_bohrium_setup_injection.py` — bus 注入 mock 改造
- `tests/matmaster/devshell/test_integration.py` — MessageBus 依赖替换
- `tests/matmaster/devshell/test_compaction_via_devshell.py` — bus 依赖替换
- `tests/matmaster/core/test_context_compactor.py` L297 — bus= 兼容分支测试改造
- `tests/matmaster/integration/test_compaction_real_api.py` L231 — bus= 兼容层测试改造
- 其他导入 MessageBus/EventRouter 的测试文件需全面审计（DBUS-01）

### Phase 34-35 产出（参考）
- `matmaster/core/agent.py` — AgentKernel._run_items() / run_stream()（generator 事件源）
- `matmaster/core/exp.py` — Exp.run_stream()（透传 generator）
- `matmaster/core/context_compactor.py` — 已改为 event_sink 模式（Phase 34 D-07），bus= 兼容分支需本 phase 清理

### 需求定义
- `.planning/REQUIREMENTS.md` — DBUS-01~03（去总线化）、ASCH-01（跳过）、FUTR-03（DevShell run_stream 迁移，不在 Phase 36 范围）

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `matmaster/integration/event_router.py` L99-111: EventRouter._dispatch() 的错误隔离模式（per-handler try/except + logging）可直接复用到 fanout 函数
- `matmaster/integration/event_router.py` L65-88: EventRouter.stop() 的 drain + close 逻辑是 fanout 生命周期管理的参考模板
- `matmaster/integration/event_router.py` L113-132: _close_handlers() 的 sync/async close 双模式处理（inspect.isawaitable pattern）
- `matmaster/integration/sse_handler.py`: SSEHandler.handle() 纯 async，可直接被 fanout 调用
- `matmaster/integration/persistence_handler.py`: PersistenceHandler.handle() 纯 async，可直接被 create_task 包装
- `matmaster/integration/workspace_handler.py` L173-183: close() 等待 ThreadPoolExecutor.shutdown()——fanout close 阶段必须调用

### Established Patterns
- EventHandler Protocol（event_router.py L28-33）: `async def handle(event: BusEvent) -> None` — fanout 可复用此 Protocol
- handler 注册顺序决定优先级（SSEHandler 排第一）
- handler 的 close() 方法用于资源清理——fanout 必须在 run 结束时执行
- `loop.call_soon_threadsafe()` 用于线程→事件循环桥接（agent_run_bohrium.py L414）

### Integration Points
- run_agent_stream() L536-554: EventRouter 创建点（改为 fanout 构造）
- run_agent_stream() L666-673: generator 事件消费循环 → bus.emit_nowait（改为 fanout 调用）
- run_agent_stream() L684-704: 后处理事件 → bus.emit_nowait（改为 fanout 调用）
- agent_run_bohrium.py L360-414: _make_event_bridge() 线程安全桥接 → 改为 fanout 同步入口
- agent_run_bohrium.py L300-309: BohriumSetupService.__init__(bus=) → 改为接受 event_sink 或 fanout 引用
- agent_worker.py L254: run_agent() 唯一外部调用者
- DevShell 4 个文件独立使用 MessageBus（需替换为轻量替代，不改变执行模型）
- context_compactor.py L141: bus= 兼容参数（Phase 34 遗留，本 phase 清理）

</code_context>

<specifics>
## Specific Ideas

- EventRouter._dispatch() 的 per-handler try/except 模式直接搬到 fanout 函数，保持错误隔离语义一致
- fanout 生命周期必须三阶段：dispatch → drain pending tasks → close handlers（参考 EventRouter.stop()）
- Bohrium 事件桥接改造是最高风险点——线程安全 + 事件不丢失 + 保持 BohriumNodeEvent 语义。建议 BohriumSetupService 接受 `event_sink: Callable[..., None]` 替代 `bus` 参数，sink 内部仍用 `loop.call_soon_threadsafe()` 调度
- DevShell 的 EventLogger 使用 log_event() 而非 handle()，不是 EventHandler Protocol 实现。替换 MessageBus 为 asyncio.Queue 或直调即可，不涉及 FUTR-03 的执行模型迁移
- run_agent_stream() 更名为 run_agent() 后，stream_service.py docstring、agent_run_bohrium.py 日志字符串、测试文件中的引用均需同步更新
- ContextCompactor bus= 兼容参数在本 phase 清理（Phase 34 遗留的 backward compat 分支）

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
