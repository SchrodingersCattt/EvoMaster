# agent_run_service.py 逐行迁移映射表

> **用途**: Phase 3-5 执行参考文档，指导 service 层从旧 evomaster 架构迁移到新 matmaster 三层管线
> **创建时间**: 2026-03-22
> **关联**: ROADMAP Phase 5 Success Criteria #8

## 总览

`agent_run_service.py` 共 ~817 行，核心方法 `run_agent_sync()` 约 500 行有效逻辑，分为 6 个阶段。

```
run_agent_sync() 阶段分解：

旧系统                              新系统 (matmaster 三层管线)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
阶段1: Playground 初始化 (L113-218)  → Playground.prepare() → PlaygroundContext
阶段2: Bohrium 凭证+SSH (L333-568)  → PlaygroundContext.run_meta + BohriumSetupService
阶段3: LLM+Agent 创建 (L569-659)    → Exp.assemble() → AgentRuntimeSpec
阶段4: 历史加载 (L671-708)          → ChatHistoryConverter (适配新 Message 类型)
阶段5: 执行 (L709-741)              → AgentKernel.run(spec, task)
阶段6: event_callback (L355-481)    → EventEmitterHook + MessageBus + EventRouter
```

---

## 阶段 1：Playground 初始化

### 逐行映射

| 行号 | 旧逻辑 | 新逻辑 | 难度 | 备注 |
|------|--------|--------|-----|------|
| L113-126 | `init_playground_sync()`: 动态导入 `playground.mat_master`，验证 config.yaml，创建 runs 目录 | `Playground.__init__(config_path)` 构造函数内完成 | 低 | 去掉动态导入，改为直接实例化 |
| L143-155 | `_get_or_create_playground()`: 按 session_id 缓存 Playground 实例 | 保留在 service 层，缓存新 Playground 实例 | 低 | 缓存逻辑不属于三层架构，留在 service |
| L156 | `get_playground_class('mat_master', config_path=...)` 工厂调用 | `MatMasterPlayground(config_path=...)` 直接构造 | 低 | 去掉注册表间接层 |
| L159 | `pg.set_run_dir(run_dir, task_id=session_id)` | `Playground.prepare(run_dir, task_id)` 内部完成 | 低 | 合并到 prepare() |
| L178-188 | `pg._mcp_progress_callback = ...` 注册 MCP 进度回调 | `PlaygroundContext.mcp_manager` 自带事件发射到 MessageBus | 中 | Phase 4 需定义 MCPManager Protocol |
| L190 | `pg.setup()` 连接 MCP servers | `Playground.prepare()` 返回 `PlaygroundContext` | 中 | setup 的副作用内聚到 prepare() |
| L196 | `_playgrounds[session_id] = pg` 缓存 | 保留 | 低 | |

### 迁移后代码

```python
# =================== 旧 ===================
pg = get_playground_class('mat_master', config_path=config_path)
pg.set_run_dir(run_dir, task_id=session_id)
pg._mcp_progress_callback = lambda *a: _emit_mcp_event_safely(...)
pg.setup()

# =================== 新 ===================
playground = MatMasterPlayground(config_path=config_path)
pg_ctx: PlaygroundContext = playground.prepare(
    run_dir=run_dir,
    task_id=session_id,
    mcp_event_sink=bus,  # MessageBus，MCP 事件直接发射到 bus
)
```

---

## 阶段 2：Bohrium 凭证与 SSH

### 逐行映射

| 行号 | 旧逻辑 | 新逻辑 | 难度 | 备注 |
|------|--------|--------|-----|------|
| L335-345 | `load_run_credentials()` → (run_creds, user_id_for_ak, org_id) | 保留，结果注入 `PlaygroundContext.run_meta` | 低 | 纯数据读取，不涉及架构 |
| L347 | `apply_run_credentials_to_session(base.session, run_creds)` | 移入 `Playground.prepare()` 内部，从 run_meta 读取 | 中 | Session 操作内聚到 Playground |
| L349-365 | `setup_bohrium_for_run()` → SSH 连接+技能同步 | 整体保留为独立 `BohriumSetupService`，输出结果写入 `PlaygroundContext.run_meta['bohrium']` | 高 | 539 行的复杂模块，不宜拆散 |
| L400-448 | SSH 连接后的凭证应用+代理清理+技能同步 | `BohriumSetupService.setup()` 内部完成，对外只暴露 `BohriumSetupResult` | 高 | |
| L471-538 | `cleanup_bohrium_after_run()` | 保留为 `BohriumSetupService.cleanup()`，在 run 结束的 finally 中调用 | 中 | |

### 关键决策：Bohrium 模块处理方式

Bohrium 模块 (539 行) 作为独立 service 保留，不拆入三层架构。通过 `PlaygroundContext.run_meta` 传递结果。

```python
# 新架构中的 Bohrium 集成点
pg_ctx = playground.prepare(...)

bohrium_svc = BohriumSetupService(sessions_service, event_bus=bus)
bohrium_result = bohrium_svc.setup(
    session_id=session_id,
    pg_ctx=pg_ctx,
    run_creds=run_creds,
)
# bohrium_result.ssh_attached → 影响 workspace 上传逻辑
```

### frozen 冲突与解决方案

PlaygroundContext 是 `frozen=True`，但 Bohrium 结果在 `prepare()` 之后才产生。

| 方案 | 描述 | 推荐 |
|------|------|------|
| A | `prepare()` 接受 bohrium_config 参数，内部完成 setup | 否（耦合 Playground 与 Bohrium） |
| B | PlaygroundContext 增加 `with_bohrium(result) -> PlaygroundContext` 方法返回新实例 | 推荐 |
| C | bohrium_result 独立传递，不写入 PlaygroundContext | 可选（需要在多处传递） |

推荐方案 B 示例：

```python
pg_ctx = playground.prepare(...)
bohrium_result = bohrium_svc.setup(session_id, pg_ctx, run_creds)
pg_ctx = pg_ctx.with_bohrium(bohrium_result)  # 返回新的 frozen 实例
```

---

## 阶段 3：LLM 配置解析 + Agent 创建

### 逐行映射

| 行号 | 旧逻辑 | 新逻辑 | 难度 | 备注 |
|------|--------|--------|-----|------|
| L569-590 | llm_override/model_override 解析：从 `pg.config_manager` 查找 LLM 配置块 | 移入 Exp 的 `assemble()` 内部 | 中 | 配置解析属于装配层职责 |
| L591 | `create_llm(LLMConfig(**cfg))` 创建 LLM 实例 | `OpenAIProvider(**cfg)` 直接构造，由 assemble() 返回在 `AgentRuntimeSpec.llm_provider` 中 | 低 | LLMConfig → OpenAIProvider 参数映射 |
| L600-640 | 创建 `StreamingMatMasterAgent(event_callback, llm, session, tools, ...)` | 完全替代为 `AgentRuntimeSpec` + `AgentKernel.run()` | 高 | 最大变更点 |
| L641 | `agent._stop_event = stop_event` | `AgentKernel.run(spec, task, stop_event=stop_event)` 参数传入 | 低 | |
| L643-650 | `agent._ask_human_queue = reply_queue` + `ConfirmationManager` | 迁移为 `ConfirmationHook` (实现 Hook Protocol)，注入 `AgentRuntimeSpec.hooks` | 中 | |

### LLMConfig → OpenAIProvider 参数映射

```python
# 旧
llm_config = pg.config_manager.get_llm_config(agent_name)
llm = create_llm(LLMConfig(**llm_config))

# 新
llm_provider = OpenAIProvider(
    model=llm_config['model'],
    api_key=llm_config['api_key'],
    base_url=llm_config.get('api_base'),
    temperature=llm_config.get('temperature', 0.7),
    max_tokens=llm_config.get('max_tokens'),
)
```

### StreamingMatMasterAgent 功能分解映射

这是迁移中最复杂的部分。旧 Agent 的每个功能需要映射到新系统的 Hook/Guard/Kernel：

| 旧 Agent 功能 | 新系统对应 | 位置 | Phase |
|---|---|---|---|
| `_on_llm_token()` → emit thought | `EventEmitterHook.on_stream_chunk()` | matmaster/engine/hooks.py | 已有 (Phase 2) |
| `_on_assistant_message()` → emit thought/assistant_state | `EventEmitterHook` + 自定义 `AssistantStateHook` | hooks 列表 | Phase 3 |
| `_on_tool_call_start()` → emit tool_call | `EventEmitterHook.pre_tool_call()` | matmaster/engine/hooks.py | 已有 (Phase 2) |
| `ToolCallbackPipeline` (before/after) | Guard (before) + Hook (before/after) | guards + hooks | Phase 3 |
| `ContextCompactor` 上下文压缩 | `AgentRuntimeSpec.compaction` 配置 + Kernel 内部处理 | Phase 3 交付 | Phase 3 |
| `JobRegistry` 异步任务管理 | 工具内部实现，不属于 Kernel | 保留在工具层 | Phase 5 |
| `_ask_human_queue` 确认机制 | `ConfirmationHook` (实现 Hook Protocol) | 新增 | Phase 3 |
| `auto_save_tool_output_patterns` | `OutputProcessorHook` (实现 Hook Protocol) | 新增 | Phase 3 |
| `summarize_patterns` 输出摘要 | `OutputProcessorHook` (实现 Hook Protocol) | 新增 | Phase 3 |
| `skill_hit` 技能命中检测 | `SkillHitHook` (实现 Hook Protocol) | 新增 | Phase 3 |

---

## 阶段 4：多轮对话历史加载

### 逐行映射

| 行号 | 旧逻辑 | 新逻辑 | 难度 | 备注 |
|------|--------|--------|-----|------|
| L674-676 | `events_table.get_session_events(session_id)` | 保留 | 无 | DB 读取不变 |
| L678-700 | `ChatHistoryConverter.events_to_dialog_messages(events)` → `list[dict]` | 适配为 `events_to_messages(events)` → `list[Message]` (matmaster.engine.types) | 中 | 返回类型变更 |
| L702-708 | 构造 `TaskInstance(task_id, 'discovery', user_prompt, meta={'dialog_history': [...]})` | 直接传 `task: str` + `history: list[Message]` 给 `AgentKernel.run()` | 中 | TaskInstance 被替代 |

### ChatHistoryConverter 适配

```python
# 旧：返回 list[dict] (OpenAI API 格式)
dialog_history = ChatHistoryConverter.events_to_dialog_messages(events)
task = TaskInstance(task_id, 'discovery', user_prompt, meta={'dialog_history': dialog_history})

# 新：返回 list[Message] (matmaster.engine.types)
history_messages: list[Message] = ChatHistoryConverter.events_to_messages(events)
kernel.run(spec, task=user_prompt, history=history_messages, stop_event=stop_event)
```

### AgentKernel.run() 签名扩展需求

当前签名只接受 `task: str`，不支持多轮对话历史注入：

```python
# 当前签名
def run(self, spec: AgentRuntimeSpec, task: str,
        stop_event: threading.Event | None = None) -> FinishEvent

# 需要扩展为
def run(self, spec: AgentRuntimeSpec, task: str,
        history: list[Message] | None = None,  # 多轮对话历史
        stop_event: threading.Event | None = None) -> FinishEvent
```

history 消息插入位置：`[SystemMessage, *history, UserMessage(task)]`

此变更影响 Phase 2 已交付的代码，建议在 Phase 3 开始时先完成此扩展。

---

## 阶段 5：实验执行

### 逐行映射

| 行号 | 旧逻辑 | 新逻辑 | 难度 | 备注 |
|------|--------|--------|-----|------|
| L709 | `exp = pg._create_exp()` → DirectSolver 或 ResearchPlanner | Phase 3 的 `DirectExp(pg_ctx)` 或 `PlannerExp(pg_ctx)` | 高 | DirectSolver 的路由逻辑需迁移到 Exp |
| L710 | `exp.run(task=task, append_result=False)` | `kernel.run(spec, task, history, stop_event)` → `FinishEvent` | 中 | |
| L719-730 | 成功分支：`use_quota()` + workspace 上传 + finish 事件 | 保留在 service 层的 finally 块中 | 低 | |
| L710-717 | 取消分支：推送 cancelled 事件 | `FinishEvent.reason == 'cancelled'` 由 Kernel 返回，service 层判断后推送 | 低 | |

### 迁移后执行流

```python
# =================== 旧 ===================
exp = pg._create_exp()  # DirectSolver 或 ResearchPlanner
exp.run(task=task_instance, append_result=False)
# 内部：agent.run(task) → trajectory
# 事件通过 agent._emit() → event_callback 回调链

# =================== 新 ===================
# Phase 3: Exp 装配
exp = DirectExp(pg_ctx)  # 或 PlannerExp
spec: AgentRuntimeSpec = exp.assemble(
    llm_override=llm_override,
    model_override=model_override,
    mode=mode,
    hooks=[
        EventEmitterHook(bus, source='MatMaster'),
        ConfirmationHook(reply_queue),
        OutputProcessorHook(auto_save_patterns, summarize_patterns),
    ],
)

# Phase 2: Kernel 执行
kernel = AgentKernel()
finish_event: FinishEvent = kernel.run(
    spec=spec,
    task=user_prompt,
    history=history_messages,
    stop_event=stop_event,
)

# service 层后处理
if finish_event.reason == 'cancelled':
    bus.emit(CancelledEvent(...))
elif finish_event.reason in ('natural', 'max_turns', 'hook_stopped'):
    await use_quota(user_id)
    _upload_workspace(...)
```

---

## 阶段 6：event_callback 拆解

旧的 `event_callback` 是 ~130 行的闭包，混合了 5 种职责。需要拆解到新架构的不同组件。

### 职责分解映射

| 职责 | 行号 | 新系统归属 | 迁移方式 |
|------|------|-----------|---------|
| 事件发射 | L355-370 | `EventEmitterHook` → `MessageBus` | 已有实现 (Phase 2) |
| 持久化决策 | L371-380 `_should_persist_event()` | `PersistenceHandler` (EventRouter 内) | 从 bus 消费事件，按规则入库 |
| 推送决策 | L381-400 `_should_skip_push()` | `SSEHandler` (EventRouter 内) | 从 bus 消费事件，按规则推送 |
| Workspace 上传触发 | L420-481 | `WorkspaceHandler` (EventRouter 内) | 监听 ToolResultEvent，防抖+快照+上传 |
| 异步/同步 send_cb 调用 | L401-419 | `SSEHandler` 内部处理 | asyncio.run_coroutine_threadsafe 或同步 |

### 新的事件消费架构

```
AgentKernel.run()
    ↓ (通过 EventEmitterHook)
MessageBus
    ↓ (消费者线程)
┌─────────────────────────────────────────────────────────┐
│ EventRouter (单一消费者，分发到多个处理器)                │
│                                                         │
│  ├─ PersistenceHandler                                  │
│  │   if should_persist(event):                          │
│  │       events_table.add_event(session_id, payload)    │
│  │                                                      │
│  ├─ SSEHandler                                          │
│  │   if should_push(event):                             │
│  │       send_cb(sse_payload)                           │
│  │       if redis: publish_stream_event(sid, payload)   │
│  │                                                      │
│  └─ WorkspaceHandler                                    │
│      if isinstance(event, ToolResultEvent):             │
│          debounce → snapshot → upload_to_oss()          │
└─────────────────────────────────────────────────────────┘
```

### 持久化规则映射

```python
# 直接映射自旧 _should_persist_event()
def should_persist(event: BusEvent) -> bool:
    # log_line 和 llm_token 不入库
    if isinstance(event, ThoughtEvent):
        if event.stream_state in ('start', 'streaming', 'end'):
            return False  # 流式 thought 中间态不入库
    # 其他事件入库
    return True
```

### 推送规则映射

```python
# 直接映射自旧 _should_skip_push()
def should_push(event: BusEvent, mode: str) -> bool:
    if isinstance(event, AssistantStateEvent):
        return False  # 内部消费，不推送
    if isinstance(event, ThoughtEvent):
        if mode == 'planner' and event.stream_state:
            return False  # Planner 流式 thought 不推送
        if mode == 'direct' and not event.stream_state:
            return False  # Direct 完整 thought 仅入库不推送
    return True
```

### Workspace 上传触发逻辑

```python
# 从旧 event_callback L420-481 提取
class WorkspaceHandler:
    _last_check_time: float = 0
    _last_snapshot: frozenset | None = None
    _debounce_seconds: float = 2.0

    def handle(self, event: BusEvent) -> None:
        if not isinstance(event, ToolResultEvent):
            return
        if self._ssh_attached:
            return  # 远程工作目录，跳过
        now = time.monotonic()
        if now - self._last_check_time < self._debounce_seconds:
            return  # 防抖
        self._last_check_time = now
        snapshot = self._get_workspace_snapshot()
        if snapshot == self._last_snapshot:
            return  # 无变化
        self._upload_workspace_to_oss()
        self._last_snapshot = snapshot
```

---

## 依赖链变更

### 删除的依赖（旧 → 无）

| 旧依赖 | 用途 | 替代方式 |
|--------|------|---------|
| `evomaster.core.get_playground_class` | 动态工厂 | 直接构造 `MatMasterPlayground` |
| `evomaster.utils.LLMConfig` | LLM 配置 DTO | `OpenAIProvider` 构造参数 |
| `evomaster.utils.create_llm` | LLM 工厂 | `OpenAIProvider(**cfg)` 直接构造 |
| `evomaster.utils.TaskInstance` | 任务封装 | 直接传 `str` + `list[Message]` |
| `playground.mat_master.ConfirmationManager` | 确认管理 | `ConfirmationHook` (Hook Protocol) |
| `playground.mat_master.StreamingMatMasterAgent` | 流式 Agent | `AgentKernel` + `EventEmitterHook` |

### 新增的依赖

| 新依赖 | 用途 | 来源 Phase |
|--------|------|-----------|
| `matmaster.engine.AgentKernel` | 执行引擎 | Phase 2 (已有) |
| `matmaster.types.AgentRuntimeSpec` | 运行时契约 | Phase 1 (已有) |
| `matmaster.types.PlaygroundContext` | 环境契约 | Phase 1 (已有) |
| `matmaster.bus.MessageBus` | 事件总线 | Phase 1 (已有) |
| `matmaster.bus.QueueBridge` | SSE 桥接 | Phase 1 (已有) |
| `matmaster.engine.EventEmitterHook` | 事件发射 | Phase 2 (已有) |
| `MatMasterPlayground` (新实现) | 环境准备 | Phase 4 交付 |
| `DirectExp` / `PlannerExp` (新实现) | 能力装配 | Phase 3 交付 |

### 保留的依赖（不变）

- `BohriumSetupService` (包装 agent_run_bohrium)
- `ChatHistoryConverter` (适配新 Message 类型)
- `quota_service`
- `sessions_service`
- `events_service`
- `redis_dao`
- `worker_registry_service`

---

## 需要新增的组件

| 组件 | 类型 | 职责 | 交付 Phase |
|------|------|------|-----------|
| `ConfirmationHook` | Hook 实现 | 包装 reply_queue，拦截需要人工确认的工具调用 | Phase 3 |
| `OutputProcessorHook` | Hook 实现 | auto_save + summarize 工具输出后处理 | Phase 3 |
| `SkillHitHook` | Hook 实现 | 技能命中追踪，发射 SkillHitEvent | Phase 3 |
| `AssistantStateHook` | Hook 实现 | 在 post_tool_call 后发射 AssistantStateEvent | Phase 3 |
| `EventRouter` | Service | 从 MessageBus 消费事件，分发到 Persistence/SSE/Workspace 处理器 | Phase 5 |
| `WorkspaceService` | Service | workspace 快照+防抖+OSS 上传 | Phase 5 |
| `BohriumSetupService` | Service | 包装现有 agent_run_bohrium 模块 | Phase 5 |

---

## AgentKernel.run() 签名扩展

当前签名不支持多轮对话历史注入，Phase 3 开始前需先扩展：

```python
# 当前 (Phase 2 交付)
def run(self, spec: AgentRuntimeSpec, task: str,
        stop_event: threading.Event | None = None) -> FinishEvent

# 需要扩展
def run(self, spec: AgentRuntimeSpec, task: str,
        history: list[Message] | None = None,
        stop_event: threading.Event | None = None) -> FinishEvent
```

消息构造逻辑：`messages = [SystemMessage(spec.system_prompt), *history, UserMessage(task)]`

---

## 迁移后 run_agent_sync() 完整伪代码

```python
def run_agent_sync(
    self,
    session_id: str,
    user_prompt: str,
    send_cb: Callable,
    loop: asyncio.AbstractEventLoop | None,
    stop_event: threading.Event,
    mode: str,
    reply_queue: ReplyQueueLike | None,
    task_id: str,
    invocation_id: str | None = None,
    llm_override: str | None = None,
    model_override: str | None = None,
) -> None:
    run_started_at = time.monotonic()
    bus = MessageBus()
    bridge = QueueBridge(bus)
    ssh_attached = False

    try:
        # ── 阶段 1: Playground 初始化 ──
        playground = self._get_or_create_playground(session_id)
        pg_ctx: PlaygroundContext = playground.prepare(
            run_dir=run_dir,
            task_id=task_id,
            mcp_event_sink=bus,
        )

        # ── 阶段 2: Bohrium 凭证 ──
        run_creds, user_id_for_ak, org_id = load_run_credentials(
            self._sessions_service, session_id
        )
        bohrium_svc = BohriumSetupService(self._sessions_service, bus)
        bohrium_result = bohrium_svc.setup(
            session_id=session_id, pg_ctx=pg_ctx,
            run_creds=run_creds, user_id_for_ak=user_id_for_ak, org_id=org_id,
        )
        ssh_attached = bohrium_result.ssh_attached
        if bohrium_result.abort_result:
            return  # 失败，已推送 error 事件
        pg_ctx = pg_ctx.with_bohrium(bohrium_result)

        # ── 阶段 3: Exp 装配 ──
        exp = DirectExp(pg_ctx)  # 或 PlannerExp(pg_ctx)
        spec: AgentRuntimeSpec = exp.assemble(
            llm_override=llm_override,
            model_override=model_override,
            mode=mode,
            hooks=[
                EventEmitterHook(bus, source='MatMaster'),
                ConfirmationHook(reply_queue),
                OutputProcessorHook(...),
                SkillHitHook(...),
            ],
        )

        # ── 阶段 4: 历史加载 ──
        events = events_table.get_session_events(session_id)
        history: list[Message] = ChatHistoryConverter.events_to_messages(events)

        # ── 启动事件路由器 (阶段 6 的消费端) ──
        router = EventRouter(
            bus=bus, bridge=bridge,
            send_cb=send_cb, loop=loop,
            session_id=session_id, task_id=task_id,
            mode=mode, ssh_attached=ssh_attached,
            events_table=events_table,
        )
        router.start()  # 后台线程消费 MessageBus

        # ── 阶段 5: 执行 ──
        kernel = AgentKernel()
        finish_event = kernel.run(
            spec=spec,
            task=user_prompt,
            history=history,
            stop_event=stop_event,
        )

        # ── 后处理 ──
        if finish_event.reason == 'cancelled':
            bus.emit(CancelledEvent(source='System'))
        else:
            await use_quota(user_id)
            self._upload_workspace(session_id, task_id, ssh_attached)

    except Exception as exc:
        bus.emit(ErrorEvent(source='System', content=str(exc)))
    finally:
        router.stop()
        bohrium_svc.cleanup(session_id=session_id, ssh_attached=ssh_attached)
        self._sessions_service.release_session_run(session_id, run_success=...)
        self._release_playground(session_id)
```

---

## 迁移风险矩阵

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| StreamingMatMasterAgent 的流式 thought 事件格式与 EventEmitterHook 不完全对齐 | 前端显示异常 | Phase 5 端到端测试必须覆盖流式场景 |
| Bohrium SSH 连接后的 frozen PlaygroundContext 无法写入结果 | 架构冲突 | 采用 `with_bohrium()` 返回新实例 |
| ChatHistoryConverter 返回类型从 `list[dict]` 变为 `list[Message]` | 下游断裂 | 提供双版本方法，渐进切换 |
| DirectSolver 的路由逻辑（SkillEvolution vs Standard）需迁移到新 Exp | 功能丢失 | Phase 3 的 DirectExp 必须保留路由 |
| ResearchPlanner 状态机（5 阶段）迁移复杂度高 | Phase 3 延期 | 先迁移 DirectExp，PlannerExp 单独排期 |
| event_callback 的 asyncio.run_coroutine_threadsafe 逻辑 | Worker 模式下事件丢失 | EventRouter 必须支持同步/异步双模式 |
| EventRouter 消费线程与 Kernel 执行线程的竞态条件 | 事件顺序错乱 | MessageBus 保证 FIFO，EventRouter 单线程消费 |

---

## 相关服务层影响评估

### stream_service.py

| 影响点 | 变更内容 | 难度 |
|--------|---------|------|
| `_send_cb` 回调 | 改为从 EventRouter 接收，不再由 event_callback 直接调用 | 中 |
| Redis `publish_stream_event` | 移入 EventRouter.SSEHandler | 低 |
| `generate_send_stream()` 的 job 构造 | 保留，但 worker 端消费逻辑对齐新管线 | 中 |

### sessions_service.py

无结构性变更。`try_acquire_session_run` / `release_session_run` 保留。

### events_service.py

`add_history_event()` 的 payload 格式可能需要适配新事件类型的序列化格式。

### chat_history.py

需要新增 `events_to_messages()` 方法返回 `list[Message]`，旧方法保留兼容。

---

## 执行建议

1. Phase 3 启动前，先完成 AgentKernel.run() 的 history 参数扩展（~2h 工作量）
2. Phase 3 优先交付 DirectExp，PlannerExp 延后（降低风险）
3. Phase 5 的 EventRouter 是最大的新增组件，建议作为 05-01-PLAN 的核心交付物
4. 迁移过程中保留旧路径，通过 feature flag 控制新旧切换
5. Phase 5 端到端测试必须覆盖：单轮直接执行、多轮对话、流式思考、Bohrium SSH、workspace 上传、确认回复、取消中断
