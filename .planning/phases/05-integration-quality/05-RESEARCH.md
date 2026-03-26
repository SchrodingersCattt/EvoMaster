# Phase 5: Integration and Quality - Research

**Researched:** 2026-03-22
**Domain:** Service layer integration, event routing, business hooks, E2E testing, migration documentation
**Confidence:** HIGH

## Summary

Phase 5 是整个重构项目的收官阶段，核心任务是将 Phase 1-4 交付的三层骨架（Playground/Exp/Kernel）与现有 service 层粘合，使 mat_master 和 minimal 两种 playground_type 能在新管线上端到端运行。这不是一个从零构建的阶段，而是一个集成阶段。所有底层组件（PlaygroundContext、AgentRuntimeSpec、AgentEvent、AgentKernel、DirectExp、Playground、MessageBus、QueueBridge、EventEmitterHook）已经就位且有测试覆盖。

主要工作分为四个维度：(1) 新增组件（EventRouter + 3 Handler、5 个业务 Hook、BohriumSetupService、WorkspaceHandler、ChatHistoryConverter 扩展），(2) 重写 agent_run_service.run_agent_sync() 为薄编排层，(3) 三层契约和端到端测试覆盖，(4) 迁移文档。

关键风险点在于 event_callback 闭包的拆解（130 行混合 5 种职责的闭包转为 EventRouter + Handler 架构）、frozen PlaygroundContext 与 Bohrium 后置结果的冲突（需 with_bohrium 方法）、以及 AgentKernel.run() 的 history 参数扩展。这些都在 MIGRATION-MAPPING.md 中有详细的逐行映射。

**Primary recommendation:** 按 "底层组件 -> 业务 Hook -> EventRouter -> service 重写 -> 测试 -> 文档" 的顺序分 plan 执行，每个 plan 交付可独立验证的增量。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** 直接替换调用链，不用 feature flag。agent_run_service 直接改写为新管线，旧代码（evomaster/、playground/mat_master/）保留在磁盘但 service 层不再调用
- **D-02:** 旧代码标记废弃——在旧模块入口加 DeprecationWarning，保留但明确标记
- **D-03:** 只处理 mat_master + minimal 两种 playground_type。x_master 请求直接报错，不走旧路径兜底
- **D-04:** 保留轻量版预加载——启动时验证 config YAML 存在且可解析（关键字段齐全），不再动态导入旧 playground 模块
- **D-05:** Phase 5 只验证 direct 模式的完整流程。PlannerExp 不在范围内，后续重写
- **D-06:** 不对 planner 模式请求做特殊处理（不报错、不降级），上线时会同步更新新的 planner
- **D-07:** 全部 5 个 Hook 实现：ConfirmationHook、OutputProcessorHook（auto_save + summarize）、SkillHitHook、AssistantStateHook
- **D-08:** 业务 Hook 独立为 `matmaster/hooks/` 包，按职责分文件（confirmation.py、output_processor.py、skill_hit.py、assistant_state.py）。EventEmitterHook 留在 engine/hooks.py（通用事件桥梁）
- **D-09:** 直接调用真实 LLM API 测试（使用 config.yaml 中的 API 配置），通过 CLI 模式触发。不录制/回放
- **D-10:** 外部依赖分层测试：单元测试全 mock（Redis/Bohrium/OSS），单独一组 integration test 需要真实环境才能跑（CI 中跳过，手动触发）
- **D-11:** E2E 验收标准：管线连通性 + 功能对齐（确认交互、事件持久化、workspace 上传触发等），但不验证具体 tool 调用结果
- **D-12:** agent_run_service.run_agent_sync() 整体重写——删掉旧方法体，按新管线伪代码重写。方法签名（12 个参数）保持不变，外部调用方零改动
- **D-13:** workspace 相关辅助方法（_upload_workspace_to_oss、_get_workspace_snapshot、_get_run_workspace_path）迁移到 WorkspaceHandler 类
- **D-14:** 周边 service 尽量全部兼容不改动。agent_run_service + chat_history（新增 events_to_messages 方法）是必要改动，stream_service/events_service/quota_service/worker_registry 保持不变，EventRouter 内部适配现有接口
- **D-15:** EventRouter 生命周期绑定单次 run——run_agent_sync 内部创建，finally 中 drain 剩余事件后 stop。MessageBus 和 Handler 上下文（session_id/task_id/mode/ssh_attached/send_cb）都是 per-run 的，跨 run 复用无收益且增加泄露风险

### Claude's Discretion
- EventRouter 内部的 Handler 注册和分发机制
- PersistenceHandler / SSEHandler 的过滤规则实现细节（已有 _should_persist_event / _should_skip_push 逻辑可直接迁移）
- WorkspaceHandler 的防抖参数和快照比对实现
- ChatHistoryConverter.events_to_messages() 的具体映射逻辑
- ConfirmationHook 与 ReplyQueueLike 的交互方式
- 迁移文档的格式和详细程度
- 集成测试的具体场景设计

### Deferred Ideas (OUT OF SCOPE)
- PlannerExp 完全重写 -- 设计理念与重构方向冲突，独立排期
- x_master playground 迁移 -- 优先 mat_master 和 minimal
- Session Protocol 抽象 -- Phase 4 延迟项
- Context compaction 集成 -- CompactionConfig 已在 spec 中，具体策略留后续
- 工具并行执行 -- Phase 2 延迟项
- 旧代码清理（删除 evomaster/playground/mat_master 中的废弃模块）-- 迁移稳定后再处理
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| MIGR-01 | mat_master 在新骨架上端到端跑通完整流程 | agent_run_service.run_agent_sync() 重写 + 业务 Hook 实现 + EventRouter 事件消费 + Bohrium 集成 + 配额扣减 |
| MIGR-02 | minimal 在新骨架上端到端跑通完整流程 | 同 MIGR-01，minimal 配置路径更简单（无 Bohrium、无 MCP），验证最小路径可用 |
| QUAL-01 | 三层契约有单元测试覆盖 | 现有 tests/matmaster/types/ 已覆盖 events/guards/runtime/context 部分，需补充序列化/验证边界用例 |
| QUAL-02 | mat_master 和 minimal 有端到端测试验证迁移正确性 | 通过 CLI 模式 + 真实 LLM API 验证管线连通性，单元测试层面 mock 所有外部依赖 |
| QUAL-03 | 迁移文档记录新旧架构差异和迁移指南 | 基于 MIGRATION-MAPPING.md 整理为用户可读的迁移文档 |
| QUAL-04 | 上游场景端到端验证 | run_interrupted 检测、RedisReplyQueue 跨 worker 确认、workspace OSS 上传、Bohrium 节点生命周期 |
| QUAL-05 | 配额扣减在新管线中正确执行 | use_quota 接口不变（async），run_agent_sync 中通过 asyncio.run_coroutine_threadsafe / asyncio.run 调用 |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pydantic | 2.x (existing) | frozen model 定义契约类型 | 项目已使用，所有契约层基于 Pydantic BaseModel |
| pytest | existing | 单元测试和集成测试 | pytest.ini 已配置，tests/ 目录已建立 |
| threading | stdlib | EventRouter 后台消费线程 | agent 在 ThreadPoolExecutor 中同步运行，EventRouter 需独立消费线程 |
| queue | stdlib | MessageBus 底层实现 | 已有实现，线程安全的同步队列 |
| asyncio | stdlib | quota_service 异步调用 | use_quota 是 async 函数，run_agent_sync 在同步线程中需 run_coroutine_threadsafe |
| logging | stdlib | 运行时日志 | 项目统一使用 Python logging |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| aiohttp | existing | quota_service HTTP 调用 | 配额扣减，接口不变 |
| redis (via redis_dao) | existing | WorkerRegistry、ReplyQueue、stop key | 跨 pod 协调，接口不变 |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| 自建 EventRouter | 直接在 run_agent_sync 中内联处理 | EventRouter 解耦更清晰，但 Phase 5 scope 更大；选 EventRouter 因为 event_callback 闭包正是当前架构问题根源 |
| asyncio.Queue | threading.Queue | agent 运行在 ThreadPoolExecutor 中同步线程，asyncio.Queue 需要额外的 event loop 传递，增加复杂度 |

**Installation:**
无新依赖需要安装，全部使用项目现有依赖和 stdlib。

## Architecture Patterns

### Recommended Project Structure (Phase 5 新增/修改)
```
matmaster/
├── hooks/                     # Phase 5 新增：业务 Hook 包
│   ├── __init__.py
│   ├── confirmation.py        # ConfirmationHook
│   ├── output_processor.py    # OutputProcessorHook (auto_save + summarize)
│   ├── skill_hit.py           # SkillHitHook
│   └── assistant_state.py     # AssistantStateHook
├── integration/               # Phase 5 新增：集成组件
│   ├── __init__.py
│   ├── event_router.py        # EventRouter + 3 Handler
│   ├── workspace_handler.py   # WorkspaceHandler (防抖+快照+OSS 上传)
│   └── bohrium_setup.py       # BohriumSetupService (包装现有模块)
├── engine/
│   ├── agent.py               # AgentKernel -- 需扩展 history 参数
│   └── hooks.py               # EventEmitterHook 保留不动
├── bus/                       # 不变
├── types/
│   └── context.py             # PlaygroundContext -- 需加 with_bohrium 方法
└── assembly/                  # 不变

src/services/
├── agent_run_service.py       # 整体重写为薄编排层
└── chat_history.py            # 新增 events_to_messages() 方法

tests/matmaster/
├── hooks/                     # 业务 Hook 单元测试
├── integration/               # EventRouter + Handler 单元测试
└── test_e2e_pipeline.py       # 端到端管线测试 (mock LLM)
```

### Pattern 1: EventRouter -- 单消费者多处理器
**What:** EventRouter 从 MessageBus 消费事件，分发到注册的 Handler 列表（PersistenceHandler、SSEHandler、WorkspaceHandler）。
**When to use:** 当 event_callback 闭包内混合了多种不相关职责时，拆分为独立 Handler 提高可测试性和可维护性。
**Example:**
```python
# EventRouter 核心结构
class EventRouter:
    """从 MessageBus 消费事件，分发到多个 Handler。

    生命周期绑定单次 run：run_agent_sync 内部创建，
    finally 中 drain 剩余事件后 stop。
    """

    def __init__(
        self,
        bus: MessageBus,
        handlers: list[EventHandler],
    ) -> None:
        self._bus = bus
        self._handlers = handlers
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._consume_loop, daemon=True
        )
        self._thread.start()

    def stop(self, drain_timeout: float = 2.0) -> None:
        self._stop_event.set()
        # drain 剩余事件
        deadline = time.monotonic() + drain_timeout
        while time.monotonic() < deadline:
            try:
                event = self._bus.get_nowait()
                self._dispatch(event)
            except queue.Empty:
                break
        if self._thread:
            self._thread.join(timeout=1.0)

    def _consume_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                event = self._bus.get(timeout=0.1)
                self._dispatch(event)
            except queue.Empty:
                continue

    def _dispatch(self, event: BusEvent) -> None:
        for handler in self._handlers:
            try:
                handler.handle(event)
            except Exception:
                logger.warning("Handler %s failed", handler, exc_info=True)
```

### Pattern 2: PlaygroundContext.with_bohrium -- frozen model 扩展
**What:** 使用 Pydantic model_copy() 返回新的 frozen 实例，解决 Bohrium 结果后置问题。
**When to use:** frozen model 需要在构造后添加额外信息时。
**Example:**
```python
# PlaygroundContext 扩展
class PlaygroundContext(BaseModel):
    model_config = ConfigDict(frozen=True)
    # ... existing fields ...

    def with_bohrium(self, result: dict[str, Any]) -> "PlaygroundContext":
        """返回包含 Bohrium 结果的新实例。"""
        updated_meta = {**self.run_meta, "bohrium": result}
        return self.model_copy(update={"run_meta": updated_meta})
```

### Pattern 3: 业务 Hook -- BaseHook 子类化
**What:** 每个业务 Hook 继承 BaseHook，只覆盖需要的 hook point 方法。
**When to use:** 需要在 Kernel 循环中注入业务逻辑时。
**Example:**
```python
class ConfirmationHook(BaseHook):
    """拦截需要人工确认的工具调用。"""

    def __init__(
        self,
        reply_queue: ReplyQueueLike | None,
        bus: MessageBus,
        timeout_sec: int = 20,
    ) -> None:
        self._reply_queue = reply_queue
        self._bus = bus
        self._timeout_sec = timeout_sec

    def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        if self._reply_queue is None:
            return HookAction.CONTINUE
        if not self._needs_confirmation(tool_call):
            return HookAction.CONTINUE
        # 发射确认请求事件
        self._bus.emit(ConfirmationRequestEvent(
            source="MatMaster",
            question=f"Allow {tool_call.name}?",
            mode="timeout",
            timeout_seconds=self._timeout_sec,
        ))
        # 阻塞等待用户回复
        reply = self._reply_queue.get(timeout=self._timeout_sec)
        if reply is None:
            return HookAction.SKIP  # 取消
        return HookAction.CONTINUE
```

### Pattern 4: AgentKernel.run() history 注入
**What:** 扩展 AgentKernel.run() 签名，支持多轮对话历史注入。
**When to use:** 多轮对话场景，从 DB 加载历史事件转换为 Message 列表。
**Example:**
```python
# AgentKernel.run() 扩展
def run(
    self,
    spec: AgentRuntimeSpec,
    task: str,
    history: list[Message] | None = None,  # 新增
    stop_event: threading.Event | None = None,
) -> FinishEvent:
    messages: list[Message] = [
        SystemMessage(content=spec.system_prompt),
        *(history or []),
        UserMessage(content=task),
    ]
    # ... 原有循环逻辑不变 ...
```

### Anti-Patterns to Avoid
- **在 run_agent_sync 中保留大闭包:** 旧 event_callback 的根本问题就是 130 行闭包混合 5 种职责。不要用新闭包替代，必须用 EventRouter + Handler 解耦
- **在 PlaygroundContext 上 setattr:** frozen model 不允许修改属性，必须用 model_copy() 返回新实例
- **在 Hook 中直接调用外部服务:** Hook 应通过 MessageBus emit 事件，由 EventRouter 中的 Handler 负责持久化/推送，保持 Hook 轻量
- **跨 run 复用 EventRouter:** D-15 明确要求 per-run 生命周期，跨 run 复用增加泄露风险

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 事件过滤规则 | 重新设计过滤逻辑 | 直接迁移 _should_persist_event / _should_skip_push | 这些规则经过生产验证，修改会引入兼容性风险 |
| SSE payload 格式 | 新建 payload 构造逻辑 | 复用 QueueBridge._to_sse_payload() | 已有 16 种事件类型的完整映射 |
| Workspace 快照比对 | 重新设计比对算法 | 迁移 _get_workspace_snapshot() 逻辑 | 现有的 (rel_path, mtime, size) frozenset 比对已验证有效 |
| Bohrium SSH 流程 | 拆散 agent_run_bohrium.py | 包装为 BohriumSetupService(setup/cleanup) | 539 行的复杂模块，内部逻辑耦合紧密，拆散风险高 |
| 配额扣减逻辑 | 修改 quota_service 接口 | 保持 use_quota(user_id) 异步接口不变 | 与上游 matmaster-tools-server API 对齐 |
| 同步/异步 send_cb 调用 | 新建分发逻辑 | 迁移 asyncio.run_coroutine_threadsafe 逻辑到 SSEHandler | 现有 L394-406 的双模式调用经过生产验证 |

**Key insight:** Phase 5 的核心价值不是发明新逻辑，而是将经过生产验证的业务逻辑从 820 行的单体 service 方法中提取并重组到结构清晰的组件中。迁移时应最大程度保留行为一致性。

## Common Pitfalls

### Pitfall 1: frozen PlaygroundContext 与 Bohrium 后置结果
**What goes wrong:** Playground.prepare() 先于 Bohrium setup 执行，但 Bohrium 结果（ssh_attached 等）需要注入 PlaygroundContext。直接 setattr 会抛 FrozenInstanceError。
**Why it happens:** Pydantic frozen=True 模型禁止属性修改。
**How to avoid:** 使用 model_copy(update=...) 返回新实例。在 PlaygroundContext 上添加 with_bohrium() 方法。
**Warning signs:** FrozenInstanceError 异常。

### Pitfall 2: AgentKernel.run() 的 history 参数遗漏
**What goes wrong:** 当前 AgentKernel.run() 签名只接受 task: str，不支持多轮对话历史注入。如果不扩展，所有对话都是单轮的。
**Why it happens:** Phase 2 交付时 history 不在 scope 内。
**How to avoid:** 在 Phase 5 第一个 plan 中就扩展 AgentKernel.run() 的签名，添加 history: list[Message] | None = None 参数。消息构造为 [SystemMessage, *history, UserMessage(task)]。
**Warning signs:** 多轮对话中 agent 不记得之前的上下文。

### Pitfall 3: EventRouter 消费线程与 Kernel 执行线程的竞态
**What goes wrong:** Kernel 执行完毕后立即调用 router.stop()，但 MessageBus 中可能还有未消费的事件。
**Why it happens:** EventEmitterHook.emit() 向 MessageBus 投递事件是非阻塞的，但 EventRouter 消费是异步的。
**How to avoid:** router.stop() 必须先 drain（消费）MessageBus 中的所有剩余事件，设置 drain_timeout 等待上限。
**Warning signs:** 最后几个事件（如 FinishEvent）丢失，前端收不到完成信号。

### Pitfall 4: asyncio.run_coroutine_threadsafe 的 loop 参数
**What goes wrong:** Worker 模式下 loop=None，如果 SSEHandler 仍尝试 run_coroutine_threadsafe 会抛异常。
**Why it happens:** run_agent_sync 有两种调用路径：(1) API 模式有 event loop，(2) Worker 模式无 event loop。
**How to avoid:** SSEHandler 必须支持双模式：loop 不为 None 时用 run_coroutine_threadsafe，否则直接同步调用 send_cb。
**Warning signs:** Worker 模式下 SSE 事件推送失败。

### Pitfall 5: DirectExp.assemble() hooks 参数冲突
**What goes wrong:** DirectExp.assemble() 内部已创建 EventEmitterHook 并放入 hooks 列表。如果 service 层也传入 hooks，可能导致重复发射事件或 hooks 被覆盖。
**Why it happens:** 当前 DirectExp.assemble() 返回的 AgentRuntimeSpec.hooks 固定为 [emitter_hook]。
**How to avoid:** DirectExp.assemble() 需要接受外部 hooks 参数（kwargs），将外部 hooks 与内部 EventEmitterHook 合并到 spec.hooks 列表。
**Warning signs:** 事件重复发射或业务 Hook 不生效。

### Pitfall 6: ChatHistoryConverter 双版本兼容
**What goes wrong:** 旧方法 events_to_dialog_messages() 返回 list[dict]（OpenAI API 格式），新方法 events_to_messages() 返回 list[Message]（matmaster 类型）。如果删除旧方法，stream_service 等可能还在调用。
**Why it happens:** 渐进迁移中旧路径可能仍有引用。
**How to avoid:** D-14 明确要求新增方法、旧方法保留。events_to_messages() 是新增的独立方法。
**Warning signs:** stream_service 中的历史回放功能异常。

### Pitfall 7: init_playground_sync 预加载逻辑残留
**What goes wrong:** 旧的 init_playground_sync() 动态导入 playground.mat_master 模块。如果不修改，启动时仍会导入旧模块。
**Why it happens:** D-04 要求保留轻量版预加载，但内容需要改为验证 config YAML。
**How to avoid:** 重写 init_playground_sync()，改为检查 configs/mat_master/config.yaml 和 configs/minimal/config.yaml 是否存在且可解析，不再 importlib.import_module 旧模块。
**Warning signs:** 启动时旧模块加载失败导致服务无法启动。

## Code Examples

### agent_run_service.run_agent_sync() 重写后的完整伪代码
```python
# Source: MIGRATION-MAPPING.md 迁移后完整伪代码
def run_agent_sync(
    self,
    session_id: str,
    user_prompt: str,
    send_cb: Callable[[dict], Any],
    loop: asyncio.AbstractEventLoop | None,
    stop_event: Any,
    mode: str,
    reply_queue: ReplyQueueLike | None,
    task_id: str,
    invocation_id: str | None = None,
    llm_override: str | None = None,
    model_override: str | None = None,
) -> None:
    run_started_at = time.monotonic()
    bus = MessageBus()
    ssh_attached = False
    router = None

    try:
        # -- Stage 1: Playground --
        playground = self._get_or_create_playground(session_id)
        pg_ctx = playground.prepare({
            "run_dir": str(_project_root / "runs" / RUN_ID_WEB),
            "task_id": task_id,
        })

        # -- Stage 2: Bohrium --
        run_creds, user_id_for_ak, org_id = load_run_credentials(
            self._sessions_service, session_id
        )
        bohrium_svc = BohriumSetupService(self._sessions_service, bus)
        bohrium_result = bohrium_svc.setup(
            session_id=session_id, pg_ctx=pg_ctx,
            run_creds=run_creds, ...
        )
        ssh_attached = bohrium_result.ssh_attached
        if bohrium_result.abort_result:
            return
        pg_ctx = pg_ctx.with_bohrium(bohrium_result)

        # -- Stage 3: Exp assembly --
        exp = DirectExp(
            llm_provider=self._build_llm_provider(pg_ctx, llm_override, model_override),
            builtin_tools=self._get_builtin_tools(pg_ctx),
            bus=bus,
            session=playground.session,
            config_dir=playground.config_path.parent,
            mcp_config=pg_ctx.run_meta.get("mcp_config"),
            skill_config=pg_ctx.run_meta.get("skill_config"),
            ...
        )
        spec = exp.assemble(pg_ctx, hooks=[
            ConfirmationHook(reply_queue, bus, ...),
            OutputProcessorHook(...),
            SkillHitHook(...),
            AssistantStateHook(bus),
        ])

        # -- Stage 4: History --
        events_table = get_chat_events_table()
        raw_events = events_table.get_session_events(session_id) if events_table else []
        history = ChatHistoryConverter.events_to_messages(raw_events)

        # -- Stage 5: EventRouter --
        router = EventRouter(bus=bus, handlers=[
            PersistenceHandler(events_table, session_id, task_id, invocation_id),
            SSEHandler(send_cb, loop, session_id, task_id, invocation_id, mode),
            WorkspaceHandler(session_id, task_id, ssh_attached, pg_ctx.archival),
        ])
        router.start()

        # -- Stage 6: Kernel --
        kernel = AgentKernel()
        finish_event = kernel.run(spec, user_prompt, history=history, stop_event=stop_event)

        # -- Post-processing --
        if finish_event.reason == "cancelled":
            bus.emit(CancelledEvent(source="System"))
        else:
            user_id = self._sessions_service.get_session_user_id(session_id)
            if user_id:
                # 配额扣减 (async)
                if loop is not None:
                    future = asyncio.run_coroutine_threadsafe(use_quota(user_id), loop)
                    future.result(timeout=10)
                else:
                    asyncio.run(use_quota(user_id))
            bus.emit(FinishEvent(source="System", status="completed"))

    except Exception as exc:
        bus.emit(ErrorEvent(source="System", message=str(exc)))
        raise
    finally:
        if router:
            router.stop()
        exp._run_cleanup_callbacks()  # MCP/Skill 资源释放
        bohrium_svc.cleanup(session_id=session_id, ssh_attached=ssh_attached)
        get_redis_dao().delete_stop_requested(session_id, task_id)
        playground_obj = self._playgrounds.pop(session_id, None)
        if playground_obj:
            playground_obj.cleanup()
        gc.collect()
```

### EventRouter Handler Protocol
```python
# EventHandler Protocol -- 所有 Handler 实现此接口
class EventHandler(Protocol):
    def handle(self, event: BusEvent) -> None: ...

class PersistenceHandler:
    """持久化事件到数据库。"""
    def __init__(self, events_table, session_id, task_id, invocation_id):
        self._events_table = events_table
        self._session_id = session_id
        self._task_id = task_id
        self._invocation_id = invocation_id

    def handle(self, event: BusEvent) -> None:
        if not self._should_persist(event):
            return
        payload = QueueBridge._to_sse_payload_static(event)  # 或内联转换
        self._events_table.add_event(
            self._session_id, event.source, event.type,
            payload.get("content"), self._task_id,
            invocation_id=self._invocation_id,
        )

    def _should_persist(self, event: BusEvent) -> bool:
        # 直接迁移自旧 _should_persist_event()
        if event.type in ("log_line", "llm_token"):
            return False
        if isinstance(event, ThoughtEvent) and event.stream_state in (
            "start", "streaming", "end"
        ):
            return False
        return True
```

### ChatHistoryConverter.events_to_messages()
```python
# 新增方法 -- 返回 matmaster Message 类型
@classmethod
def events_to_messages(cls, events: list[dict]) -> list[Message]:
    """将 DB 事件列表转换为 matmaster Message 列表。

    与 events_to_dialog_messages() 逻辑相同，但返回
    matmaster.engine.types.Message 实例而非序列化 dict。
    """
    from matmaster.engine.types import (
        AssistantMessage as MMAssistantMessage,
        ToolMessage as MMToolMessage,
        UserMessage as MMUserMessage,
    )
    dialog_dicts = cls.events_to_dialog_messages(events)
    messages: list[Message] = []
    for d in dialog_dicts:
        role = d.get("role")
        if role == "user":
            messages.append(MMUserMessage(content=d.get("content", "")))
        elif role == "assistant":
            msg = MMAssistantMessage(content=d.get("content"))
            if d.get("tool_calls"):
                # 转换 tool_calls 格式
                from matmaster.engine.types import ToolCallData
                tcs = []
                for tc in d["tool_calls"]:
                    func = tc.get("function", {})
                    tcs.append(ToolCallData(
                        id=tc.get("id", ""),
                        name=func.get("name", ""),
                        arguments=json.loads(func.get("arguments", "{}")),
                    ))
                msg = MMAssistantMessage(content=d.get("content"), tool_calls=tcs)
            messages.append(msg)
        elif role == "tool":
            messages.append(MMToolMessage(
                content=d.get("content", ""),
                tool_call_id=d.get("tool_call_id", ""),
                tool_name=d.get("name", ""),
            ))
    return messages
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| event_callback 闭包 (130 行) | EventRouter + 3 Handler | Phase 5 | 职责解耦，可独立测试 |
| StreamingMatMasterAgent | AgentKernel + EventEmitterHook + 业务 Hook | Phase 2/5 | 移除流式 agent 子类 |
| get_playground_class 工厂 | Playground(config_path) 直接构造 | Phase 4/5 | 移除动态导入和注册表 |
| TaskInstance + dialog_history meta | task: str + history: list[Message] | Phase 5 | 类型安全，移除 dict 传递 |
| pg._create_exp() 内部创建 | service 层显式创建 DirectExp | Phase 5 | 可见性和可测试性提升 |

**Deprecated/outdated:**
- StreamingMatMasterAgent -- 完全由 AgentKernel + Hook 替代
- evomaster.utils.TaskInstance -- 由 str + list[Message] 替代
- evomaster.core.get_playground_class -- 由 Playground 直接构造替代
- evomaster.utils.create_llm / LLMConfig -- 由 OpenAIProvider 直接构造替代

## Open Questions

1. **DirectExp.assemble() 如何接受外部 hooks**
   - What we know: 当前 assemble() 内部创建 EventEmitterHook 并硬编码为 hooks=[emitter_hook]
   - What's unclear: service 层的业务 Hook（ConfirmationHook 等）如何注入到 spec.hooks
   - Recommendation: assemble() 通过 kwargs 接受 hooks 参数，与内部 EventEmitterHook 合并。修改 DirectExp.assemble() 签名增加 hooks: list[Hook] | None = None 参数

2. **BohriumSetupService 的返回类型**
   - What we know: 旧 setup_bohrium_for_run() 返回 NamedTuple(ssh_attached, abort_result)
   - What's unclear: 新 BohriumSetupService 是否需要更丰富的返回类型（如 node_id、SSH 凭证等）
   - Recommendation: 保持与旧接口一致的 NamedTuple 返回，with_bohrium() 将整个 result 作为 dict 写入 run_meta

3. **_get_or_create_playground 在新架构下的形态**
   - What we know: 旧版本按 session_id 缓存 Playground 实例，每次 run 后 pop + cleanup
   - What's unclear: 新 Playground 是否仍需要 session_id 级缓存
   - Recommendation: 保留缓存模式（D-12 要求方法签名不变），但缓存的是新 Playground 实例而非旧 pg

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (version in .venv) |
| Config file | pytest.ini |
| Quick run command | `.venv/bin/python -m pytest tests/matmaster/ -x -q` |
| Full suite command | `.venv/bin/python -m pytest tests/ -x` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MIGR-01 | mat_master 端到端管线 | integration | `.venv/bin/python -m pytest tests/matmaster/integration/test_e2e_mat_master.py -x` | Wave 0 |
| MIGR-02 | minimal 端到端管线 | integration | `.venv/bin/python -m pytest tests/matmaster/integration/test_e2e_minimal.py -x` | Wave 0 |
| QUAL-01 | 三层契约单元测试 | unit | `.venv/bin/python -m pytest tests/matmaster/types/ -x` | Partial (events/guards/runtime/context exist, need serialization edge cases) |
| QUAL-02 | 新旧路径功能对齐 | integration | `.venv/bin/python -m pytest tests/matmaster/integration/test_pipeline_alignment.py -x` | Wave 0 |
| QUAL-03 | 迁移文档 | manual-only | N/A -- documentation review | N/A |
| QUAL-04 | 上游场景验证 | unit + integration | `.venv/bin/python -m pytest tests/matmaster/integration/test_upstream_scenarios.py -x` | Wave 0 |
| QUAL-05 | 配额扣减正确性 | unit | `.venv/bin/python -m pytest tests/matmaster/integration/test_quota_pipeline.py -x` | Wave 0 |

### Existing Test Coverage (Phase 1-4)
已有测试文件覆盖 Phase 1-4 交付物：
- tests/matmaster/types/test_events.py -- BusEvent 类型测试
- tests/matmaster/types/test_guards.py -- Guard Protocol 测试
- tests/matmaster/types/test_runtime.py -- AgentRuntimeSpec 测试
- tests/matmaster/types/test_context.py -- PlaygroundContext 测试
- tests/matmaster/bus/test_message_bus.py -- MessageBus 测试
- tests/matmaster/bus/test_queue_bridge.py -- QueueBridge 测试
- tests/matmaster/engine/test_agent.py -- AgentKernel 测试
- tests/matmaster/engine/test_hooks.py -- Hook/EventEmitterHook 测试
- tests/matmaster/engine/test_guard_pipeline.py -- GuardPipeline 测试
- tests/matmaster/assembly/test_direct_exp.py -- DirectExp 测试
- tests/matmaster/assembly/test_exp.py -- Exp base class 测试
- tests/matmaster/playground/test_playground.py -- Playground 测试
- tests/matmaster/playground/test_playground_config_paths.py -- 配置路径测试

### Sampling Rate
- **Per task commit:** `.venv/bin/python -m pytest tests/matmaster/ -x -q`
- **Per wave merge:** `.venv/bin/python -m pytest tests/ -x`
- **Phase gate:** Full suite green before /gsd:verify-work

### Wave 0 Gaps
- [ ] `tests/matmaster/hooks/__init__.py` -- 包初始化
- [ ] `tests/matmaster/hooks/test_confirmation.py` -- ConfirmationHook 单元测试
- [ ] `tests/matmaster/hooks/test_output_processor.py` -- OutputProcessorHook 单元测试
- [ ] `tests/matmaster/hooks/test_skill_hit.py` -- SkillHitHook 单元测试
- [ ] `tests/matmaster/hooks/test_assistant_state.py` -- AssistantStateHook 单元测试
- [ ] `tests/matmaster/integration/__init__.py` -- 包初始化
- [ ] `tests/matmaster/integration/test_event_router.py` -- EventRouter + Handler 单元测试
- [ ] `tests/matmaster/integration/test_workspace_handler.py` -- WorkspaceHandler 单元测试
- [ ] `tests/matmaster/integration/test_e2e_mat_master.py` -- mat_master 端到端测试 (mock LLM)
- [ ] `tests/matmaster/integration/test_e2e_minimal.py` -- minimal 端到端测试 (mock LLM)
- [ ] `tests/matmaster/integration/test_upstream_scenarios.py` -- 上游场景测试 (run_interrupted, quota, workspace upload)
- [ ] 补充 tests/matmaster/types/ 中契约序列化边界用例

## Sources

### Primary (HIGH confidence)
- 项目源码直接审阅: matmaster/ 包所有模块、src/services/agent_run_service.py (820 行)
- `.planning/phases/05-integration-quality/MIGRATION-MAPPING.md` -- 逐行迁移映射表
- `.planning/phases/05-integration-quality/05-CONTEXT.md` -- 用户决策 D-01 ~ D-15
- `.planning/REQUIREMENTS.md` -- MIGR-01/02, QUAL-01~05
- 现有测试代码: tests/matmaster/ 40+ 个测试用例

### Secondary (MEDIUM confidence)
- Pydantic v2 model_copy() 方法 -- 用于 frozen model 的 with_bohrium 模式
- Python threading 模块 -- EventRouter 消费线程实现

### Tertiary (LOW confidence)
- None -- 所有发现均基于项目源码直接审阅

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- 全部使用项目现有依赖和 stdlib
- Architecture: HIGH -- 基于 MIGRATION-MAPPING.md 的逐行映射和 05-CONTEXT.md 的明确决策
- Pitfalls: HIGH -- 基于源码审阅识别的具体风险点

**Research date:** 2026-03-22
**Valid until:** 无过期限制（项目内部重构，不依赖外部版本变化）
