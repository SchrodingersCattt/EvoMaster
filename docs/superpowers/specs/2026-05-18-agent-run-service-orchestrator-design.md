# AgentRunService 编排器收敛设计

- Date: 2026-05-18
- Status: Draft（待审阅）
- Author: Kealdoom + Claude
- 前置依赖:
  - [2026-05-18-run-meta-refactor.md](../plans/2026-05-18-run-meta-refactor.md) P2 必须先 merge
  - R6 active skills boundary 已 merge（SkillResolver 已显式参数化）
- 影响范围: `src/services/agent_run_service.py`、`matmaster/core/exp.py`、
  `matmaster/core/runtime_context_assembly.py`、
  `src/services/image_input_service.py`、
  `matmaster/types/runtime_ports.py`，新增 `src/services/figure_coordinator.py`

## 1. 背景

### 1.1 当前形态

[agent_run_service.py](../../../src/services/agent_run_service.py) 是 MatMaster
生产链路的入口编排器，当前实现约 770 行。`AgentRunService.run_agent` 单方法约 500 行，
按注释划分为 7 个 Stage 加 post-processing 和 finally 清理：

- Stage 1: Playground 准备
- Stage 2: RunEventFanout 启动
- Stage 3: Bohrium 凭据 / SSH
- Stage 4: Exp 装配（LLM 路由 / vision / provider / Exp 实例）
- Stage 4 中段: Figure 协调器闭包 + Checkpoint 工厂
- Stage 4b: AskQuestion bridge
- Stage 5: History wiring
- Stage 5b: ContextAssembler 装配 + intent 解析 + user_turn_context 渲染与持久化
- Stage 5b 收尾: Skill resolver 装配 + 热缓存
- Stage 6: 生成器事件流主循环
- Post-processing + finally

### 1.2 仓库已存在的装配模块

矩阵盘点显示，[agent_run_service.py](../../../src/services/agent_run_service.py)
所做的大部分装配在仓库别处已经有现成实现，但仍在 service 层重复执行：

**matmaster 包内（运行时装配）：**

- [matmaster/providers/llm_factory.py:build_provider](../../../matmaster/providers/llm_factory.py) —
  LLM provider 装配，内部已自封装 `resolve_route` + `get_profile`。
- [matmaster/core/runtime_context_assembly.py:build_runtime_context_assembly](../../../matmaster/core/runtime_context_assembly.py) —
  装配 `ContextAssembler` + `ContextCompactor` + 端口。
- [matmaster/core/runtime_context_assembly.py:build_session_context_factory](../../../matmaster/core/runtime_context_assembly.py) —
  `SessionContextBuilder` 工厂。
- [matmaster/core/exp.py:Exp.build_runtime](../../../matmaster/core/exp.py) —
  顶层装配器。`exp.py:479` 已调用 `build_runtime_context_assembly`，构造了自己的
  `ContextAssembler` 实例。

**src/services 内（服务层装配）：**

- [src/services/context_assembly_factory.py:build_context_assembler](../../../src/services/context_assembly_factory.py)
- [src/services/context_turn_intent.py:resolve_turn_context_intent](../../../src/services/context_turn_intent.py)
- [src/services/user_turn_context_service.py:write_user_turn_context_event](../../../src/services/user_turn_context_service.py)
- [src/services/image_input_service.py:ImageInputService.ensure_vision_supported](../../../src/services/image_input_service.py)
- [src/services/skill_registry_factory.py:build_skill_registry](../../../src/services/skill_registry_factory.py)
- [src/services/skill_resolver.py:SkillRegistryResolver](../../../src/services/skill_resolver.py)
- [src/services/response_figures_service.py:ResponseFiguresAccumulator](../../../src/services/response_figures_service.py)
- [src/services/agent_run_bohrium_stage.py:run_bohrium_stage](../../../src/services/agent_run_bohrium_stage.py) —
  stage 函数样板。
- [src/services/agent_run_history_wiring.py:build_history_wiring](../../../src/services/agent_run_history_wiring.py)

### 1.3 三个具体冗余

**冗余 1：Stage 4 LLM 路由解析被算两次。**
[agent_run_service.py:351-364](../../../src/services/agent_run_service.py) 调用
`llm_config.resolve_route(...)` + `llm_config.get_profile(...)` 拿 `vision_detail`
构造 image_parts；紧接着 `build_provider(...)`
（[llm_factory.py:35-40](../../../matmaster/providers/llm_factory.py)）内部又走了一遍
`resolve_route + get_profile`。service 层的两次调用仅是为了取 `selected_profile.vision_detail`，
这是 [ImageInputService](../../../src/services/image_input_service.py) 应该处理的事。

**冗余 2：ContextAssembler 装配两份。**
[agent_run_service.py:532](../../../src/services/agent_run_service.py) 调
`build_context_assembler(...)` 拿到一个 assembler，用于本 turn 的 user prompt 装配；
[exp.py:479](../../../matmaster/core/exp.py) 又调 `build_runtime_context_assembly(...)`
拿到另一个 assembler，给 `ContextCompactor` 用。两份 assembler 用相同的 skill_resolver、
相同的 events_table（只是端口包装不同：service 层用
[AppSessionEventsPort](../../../src/services/context_assembly_ports.py)，runtime 层用
`ctx.runtime_ports.compaction.history`）。

**冗余 3：Skill resolver 装配 + 失效热缓存。**
[agent_run_service.py:528](../../../src/services/agent_run_service.py) 调
`_build_skill_resolver(exp_config, session)` 构造 resolver 传给 `exp.run_stream`。
该函数仅依赖 `exp_config.skills` 与 `pg_ctx.session`——`Exp.build_runtime` 内部
（[exp.py:760](../../../matmaster/core/exp.py)）本身就在构造 `skill_registry`，
完全可以同时产出 resolver。同时
[agent_run_service.py:_active_skills](../../../src/services/agent_run_service.py) 热缓存
（`AgentRunService.__init__` 字段）几乎不可能命中：服务多实例 + Worker pool 模式下，
缓存命中率取决于 sticky routing；即便命中，节省的也仅是一次内存级
`decode_session_events + scan_skill_hits`（毫秒级），相对一次 LLM 调用收益接近零；
更糟的是引入状态污染面（漏一次 `_remember_skill_hit` 即与 DB 永久不一致）。

### 1.4 为什么现在是合适时机

[run-meta-refactor](../plans/2026-05-18-run-meta-refactor.md) P2 完成后：

- `PlaygroundContext.metadata: RunMetadata` 是 typed BaseModel
- `PlaygroundContext.session_id` 顶层显式字段
- `PlaygroundRuntimePorts` 已含 `figure_upload`、`bohrium`、`child_event_forward_sink`、
  `compaction` 四个子 port
- `AgentRuntimeSpec.run_identity` + `turn_input` 替代 `meta` dict
- `Playground.prepare(metadata: RunMetadata, *, session_id, ...)` 强制 typed 入参

这意味着所有"夹带数据"的 dict 通道都已经被典型化。本设计要把"夹带能力"的装配代码也
按同样思路收敛——能下沉到 Exp 的下沉，能复用的复用，service 层只剩纯编排。

## 2. 目标

- 把 `AgentRunService.run_agent` 从约 500 行缩到约 150 行，函数体只剩 Stage 调用、事件循环、
  post-processing、finally 清理。
- 删除所有重复装配点：LLM 路由解析、ContextAssembler 构造、skill_resolver 构造、
  user_turn_context 写入路径，全部交给已有模块。
- 把"turn 渲染 + intent 解析 + 持久化写入"整体下推到 Exp 内部，service 层不再
  直接接触 `ContextAssembler`、`TurnInput` 构造、`resolve_turn_context_intent`、
  `write_user_turn_context_event`。
- 删除 `_active_skills` 热缓存，由 `ContextAssembler` 在已有事件读取路径上产出
  `active_skills`，不再做额外 DB 扫描。
- Figure 协调逻辑从闭包堆迁到 `FigureCoordinator` 类，与
  [run_bohrium_stage](../../../src/services/agent_run_bohrium_stage.py) 风格统一。
- 建立 boundary 测试，防止已下沉的职责回流到 service 层。

## 3. 非目标

- 不重新设计 `ContextAssembler`、`Exp`、`Fanout`、`Bohrium` 等子系统的内部行为；本设计只调整
  调用关系与职责归属。
- 不引入新的依赖注入框架。继续沿用现有"工厂函数 + 闭包 + Pydantic frozen model"模式。
- 不动 SSE / Redis / 持久化协议、Bohrium 接入、SubAgent spawn 链路。
- 不动 [HookExecutor](../../../matmaster/core/hooks.py) 或 [RuntimeTopology](../../../matmaster/types/topology.py)
  装配。
- 不引入 deprecated warning。`run-meta-refactor` 的"要么改干净，要么不动"原则在此沿用。
- 不在 Exp 内引入对 `src/services` 的反向 import。任何 service 层提供的能力必须通过
  `PlaygroundRuntimePorts` 注入。

## 4. 现状盘点：职责矩阵

下表列出 `run_agent` 各 Stage 当前的实际职责，标注本设计的去向：

| Stage | 当前职责 | 性质 | 本设计去向 |
|---|---|---|---|
| 1 | `Playground.prepare()` + `events_table` 获取 | 编排 | 保留在 service |
| 2 | `RunEventFanout` 构造 + handler 注册 | 编排 | 保留（提取小工厂） |
| 3 | `run_bohrium_stage` 调用 | 编排 | 保留（已是 stage 函数） |
| 4 (LLM) | `resolve_route` + `get_profile` + `build_provider` | 装配 | 下沉到 `ImageInputService` + `build_provider` |
| 4 (vision) | image_parts 构造写入 `TurnInput.images` | 装配 | 下沉到 `ImageInputService.build_vision_image_parts` |
| 4 (Exp) | `Exp(exp_config)` 实例化 + cancel_token 注入 | 编排 | 保留（一行） |
| 4 中段 | Figure 累积器 + 锁 + 3 个闭包 | 装配 | 提取 `FigureCoordinator` 类 |
| 4 中段 | `HistoryCheckpointService` 构造 + `_checkpoint_sink_factory` 闭包 | 装配 | 保留（service 持有 sink factory） |
| 4 中段 | `_child_event_sink` 闭包 | 装配 | 由 `FigureCoordinator` 提供 |
| 4b | `AskQuestionBridge` 构造 + `_interaction_event_sink` 闭包 | 装配 | 提取小工厂 `build_interaction_bridge` |
| 5 | `build_history_wiring` 调用 + `runtime_ports` 合并 | 编排 | 保留 |
| 5b | `_build_skill_resolver` 调用 | 装配 | 删除（Exp 内部接管） |
| 5b | `build_context_assembler` 调用 | 装配 | 删除（复用 Exp 内部的 assembler） |
| 5b | `resolve_turn_context_intent` 调用 | 装配 | 下沉到 `ContextAssembler`（带 events port） |
| 5b | `TurnInput` 默认构造 | 装配 | 下沉到 Exp（从 `ctx.metadata.turn_input` 读） |
| 5b | `assemble_turn` 调用 + payload 构造 + `write_user_turn_context_event` | 装配 | 下沉到 Exp（通过 `user_turn_context_writer` port 回调 service） |
| 5b 收尾 | `_resolve_active_skill_names` + 热缓存 + `_remember_skill_hit` | 装配 | 删除（`ContextAssembler` 已读 events，可一并产出） |
| 6 | 生成器主循环 + source 规范化 + figure dispatch + RunResultEvent 检测 | 编排 | 保留（缩短） |
| post | StreamClosedEvent / ErrorEvent / quota 扣费 | 编排 | 提取 `_finalize_run` 私有方法 |
| finally | Bohrium cleanup + Fanout drain + stop_requested 清理 + Playground 释放 | 编排 | 提取 `_cleanup_run` 私有方法 |

## 5. 锁定的架构决策

| # | 决策点 | 选择 | 放弃的候选 | 理由 |
|---|---|---|---|---|
| D1 | turn 装配归属 | 完全下推到 `Exp.run_stream`，service 不再直接持有 `ContextAssembler` | 留在 service 但提取成 `run_turn_context_stage` 函数 | Exp 内已有同一份 `ContextAssembler` 实例（[exp.py:479](../../../matmaster/core/exp.py)）；service 层再构造一份是真冗余，不是抽象失衡 |
| D2 | `user_turn_context_writer` 注入方式 | 通过 `PlaygroundRuntimePorts.user_turn_context_writer` 新 port 注入回调 | Exp 直接 import `src/services/user_turn_context_service` | 不允许核心层反向 import 服务层（[runtime-ports-run-meta-design.md:96-97](2026-05-10-runtime-ports-run-meta-design.md) 已确立单向依赖原则） |
| D3 | intent 解析归属 | 移到 `ContextAssembler.assemble_turn` 内部，需要 `events_port` 参数 | 保留 `src/services/context_turn_intent.py` 模块，service 调用后传给 Exp | intent 解析逻辑（[context_turn_intent.py:8-28](../../../src/services/context_turn_intent.py)）只需 `events_port` 与 `instructions_hash`，运行时层都有；service 层调用是历史包袱 |
| D4 | skill_resolver 装配归属 | `Exp.build_runtime` 内部基于 `self._skill_registry` 产出 resolver，`run_stream` 删除 `skill_resolver` 参数 | service 持续构造并通过 `run_stream(skill_resolver=...)` 传入 | `_build_skill_resolver`（[agent_run_service.py:143-180](../../../src/services/agent_run_service.py)）只依赖 `exp_config.skills` 与 `ctx.session`，service 层零额外贡献 |
| D5 | 热缓存 `_active_skills` | 直接删除，不替代 | 保留缓存但改为 LRU + invalidate hook | 命中率极低 + 状态污染面大；删除后由 `ContextAssembler` 在已有事件读取路径上一并产出 `active_skills`，零额外成本 |
| D6 | `active_skills` 产出位置 | `ContextAssembler.assemble_turn` 返回的 `AssemblyResult` 新增 `active_skills: frozenset[str]` 字段 | service 在 Stage 5b 收尾继续单独扫描 | 已经手握 session 事件，复用即可 |
| D7 | vision/image_parts 装配归属 | `ImageInputService` 加 `build_vision_image_parts(llm_config, images, *overrides)` 一次返回 image_parts；service 层只调一次 | 拆成 `ensure_vision_supported` + 手工构造 image_parts 两步 | 现状两步耦合，service 层重复 `resolve_route + get_profile`；合并后 service 层 image 分支降到 3 行 |
| D8 | LLM provider 装配 | 保留 `build_provider`（一行调用），service 层删除 `resolve_route + get_profile` | 把 build_provider 也包到一个 stage 函数 | `build_provider` 已经是单一职责函数；包一层只增加间接 |
| D9 | Figure 协调器形态 | 提取 `FigureCoordinator` 类（class），不再用 3 个闭包 | 提取 `figure_coordinator_factory` 工厂函数返回 `(accumulator, dispatch_fn, record_fn, sink)` 元组 | 三个方法共享 accumulator + lock + fanout，状态明显，类是自然形态；闭包堆迫使 caller 知道 3 个回调名 |
| D10 | `FigureCoordinator` 位置 | `src/services/figure_coordinator.py`（与 service 同层） | `matmaster/integration/figure_coordinator.py` | 协调对象需要操作 `RunEventFanout` 与 `flush_persistence_barrier`，属于服务层基础设施；matmaster 包不应感知 fanout |
| D11 | `AskQuestionBridge` 装配 | 提取小工厂 `build_interaction_bridge(session_id, fanout)` 返回 bridge 实例 | 保留 service 内联构造 | 内联只 4 行；提取一个工厂便于测试替身注入，但代价低 |
| D12 | `Exp.run_stream` 入参变化 | 删除 `user_prompt`、`skills`、`skill_resolver` 参数；改读 `ctx.metadata.turn_input` 与 `ctx.runtime_ports.user_turn_context_writer` | 保留 user_prompt 入参作为兼容 | run-meta-refactor P2 后 `turn_input` 已经在 `ctx.metadata`，再传 user_prompt 是双轨；本设计假定可破坏性变更 |
| D13 | 删除已下沉的 service 文件 | 保留 `context_assembly_factory.py` 和 `context_turn_intent.py` 直到 Exp 内部接管完成，再一次性删除；不留 deprecated 入口 | 把它们改为薄壳调用 Exp 内部函数 | 与 run-meta-refactor 的"不留兼容入口"原则一致 |
| D14 | boundary 测试策略 | 新增 `tests/matmaster/services/test_agent_run_service_orchestration_boundary.py`，断言 service 层不再 import `ContextAssembler` / `SkillResolver` / `resolve_turn_context_intent` / `write_user_turn_context_event` | 仅用 grep 检查 | grep 易漏；ast 反射或 `import` 断言更可靠 |

## 6. 推荐架构

### 6.1 分层

```
┌─────────────────────────────────────────────────────────────┐
│ src/services/agent_run_service.py (~150 行)                │
│  - run_agent: stage 编排 + 事件循环 + finalize + cleanup   │
│  - 持有 FigureCoordinator、AskQuestionBridge、Fanout       │
│  - 通过 RuntimePorts 注入能力给 Exp                        │
└──────────────────────┬──────────────────────────────────────┘
                       │ 通过 PlaygroundContext + RuntimePorts
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ matmaster/core/exp.py:Exp                                  │
│  - build_runtime: 装配 ContextAssembler / skill_resolver / │
│    tools / kernel                                          │
│  - run_stream: 接管 turn 渲染 + intent 解析 + 持久化写入   │
│    （通过 user_turn_context_writer port 回调 service）     │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ matmaster/context/assembly.py:ContextAssembler             │
│  - assemble_turn: 加返回 active_skills + 接受 intent       │
│    解析子函数                                              │
└─────────────────────────────────────────────────────────────┘
```

### 6.2 agent_run_service.py 目标形态

```python
class AgentRunService:
    def __init__(self, sessions_service=None):
        self._sessions_service = sessions_service or get_sessions_service()
        self._pg_manager = PlaygroundManager(_project_root)
        # 删除 _active_skills

    def init_playground_sync(self) -> None:
        self._pg_manager.validate_startup()

    async def run_agent(
        self,
        session_id: str,
        user_prompt: str,
        send_cb,
        cancel_token,
        mode: str,
        task_id: str,
        invocation_id: str | None = None,
        llm_override: str | None = None,
        model_override: str | None = None,
        images: list[str] | None = None,
        turn_input: TurnInput | None = None,
        bohrium_required: bool = False,
        remote_workdir: str | None = None,
    ) -> tuple[bool | tuple[bool, str], int]:
        run_started_at = time.monotonic()
        fanout: RunEventFanout | None = None
        bohrium_svc = None
        ssh_attached = False
        playground = None
        try:
            # Stage 1: Playground
            self.init_playground_sync()
            task_id = task_id or ('ws_' + uuid.uuid4().hex[:16])
            playground = self._pg_manager.get_or_create(session_id)
            run_dir = str(_project_root / 'runs' / RUN_ID_WEB)
            pg_ctx = playground.prepare(
                RunMetadata(run_dir=run_dir, task_id=task_id),
                session_id=session_id,
            )
            try:
                events_table = get_chat_events_table()
            except Exception:
                return ((False, 'pre_router_setup_failed'), 0)

            # Stage 2: Fanout + Figure coordinator + Bridge
            fanout = self._build_fanout(send_cb, events_table, session_id, task_id, invocation_id, mode)
            figure_coordinator = FigureCoordinator(
                fanout=fanout, session_id=session_id, task_id=task_id,
            )
            bridge = build_interaction_bridge(session_id, fanout)

            # Stage 3: Bohrium
            loop = asyncio.get_running_loop()
            stage_result = await run_bohrium_stage(
                sessions_service=self._sessions_service,
                fanout=fanout,
                dispatch_from_thread=lambda evt: fanout.dispatch_from_thread(loop, evt),
                session_id=session_id, task_id=task_id,
                playground=playground, pg_ctx=pg_ctx,
                run_started_at=run_started_at,
                bohrium_required=bohrium_required, remote_workdir=remote_workdir,
            )
            if stage_result.abort_result is not None:
                return stage_result.abort_result
            pg_ctx = stage_result.pg_ctx
            bohrium_svc = stage_result.bohrium_svc
            ssh_attached = stage_result.ssh_attached

            # Stage 4: LLM provider + vision（image_parts 装配下沉）
            exp_config = load_exp_config(mode or 'direct')
            llm_config = load_llm_config(_MATMASTER_CONFIG_DIR / 'llm_config.yaml')
            agent_default_llm = _get_agent_default_llm()
            if images:
                image_parts = get_image_input_service().build_vision_image_parts(
                    llm_config=llm_config, images=images,
                    model_override=model_override, llm_override=llm_override,
                    default_profile_key=agent_default_llm,
                )
            else:
                image_parts = ()
            pg_ctx = pg_ctx.model_copy(update={
                'llm_provider': build_provider(
                    llm_config,
                    model_override=model_override, llm_override=llm_override,
                    default_profile_key=agent_default_llm,
                ),
                'llm_config': llm_config,
                'interaction_bridge': bridge,
            })

            # Stage 5: 接线
            checkpoint_service = HistoryCheckpointService(events_table) if events_table else None
            wiring = build_history_wiring(
                base_runtime_ports=pg_ctx.runtime_ports,
                events_table=events_table,
                session_id=session_id, task_id=task_id,
                raw_history_limit=_DIALOG_HISTORY_MAX_EVENTS,
                child_event_sink=figure_coordinator.child_event_sink,
                checkpoint_sink_factory=self._make_checkpoint_sink_factory(
                    checkpoint_service, fanout, session_id, task_id, invocation_id,
                ),
                pre_compaction_barrier=fanout.flush_persistence_barrier,
            )
            pg_ctx = pg_ctx.with_runtime_ports(wiring.runtime_ports)
            pg_ctx = pg_ctx.with_runtime_port(
                figure_upload=FigureUploadPort(config=figure_coordinator.upload_config),
                user_turn_context_writer=UserTurnContextWriterPort(
                    write=self._make_user_turn_context_writer(
                        events_table, session_id, task_id, invocation_id,
                    ),
                ),
            )
            pg_ctx = pg_ctx.with_metadata(
                turn_input=turn_input or TurnInput.from_values(
                    user_text=user_prompt,
                    images=tuple(p['url'] for p in image_parts if p.get('url')),
                ),
                user_instructions=stage_result.user_instructions,
            )

            # Stage 6: 编排循环
            exp = Exp(exp_config)
            if pg_ctx.session is not None:
                pg_ctx.session._cancel_token = cancel_token

            run_result_event = None
            async with aclosing(
                exp.run_stream(pg_ctx, cancel_token=cancel_token)
            ) as stream:
                async for event in stream:
                    event = _normalize_event_source(event)
                    if isinstance(event, RunResultEvent) and event.spawn_id is None:
                        await figure_coordinator.flush_if_dirty('final_flush')
                    await fanout.dispatch(event)
                    if isinstance(event, ToolResultEvent):
                        await figure_coordinator.record_tool_result(
                            event, include_spawned=False, reason='tool_result',
                        )
                    if isinstance(event, RunResultEvent):
                        run_result_event = event

            return await self._finalize_run(
                fanout, run_result_event, session_id, model_override, run_started_at,
            )
        except Exception as exc:
            logger.exception('run_agent error: session_id=%s', session_id)
            if fanout is not None:
                with suppress(Exception):
                    await _emit_error_and_close_fanout(fanout, str(exc))
            return ((False, str(exc)), int((time.monotonic() - run_started_at) * 1000))
        finally:
            await self._cleanup_run(
                bohrium_svc, fanout, playground,
                ssh_attached, session_id, task_id, run_started_at,
            )
```

注：上述代码省略了已抽出的辅助方法实现（`_build_fanout`、`_make_checkpoint_sink_factory`、
`_make_user_turn_context_writer`、`_finalize_run`、`_cleanup_run`）。这些方法约 30-50 行总长，
保持 run_agent 主体不超过 150 行。

## 7. 装配下沉方案

### 7.1 LLM provider 装配

**当前状态：**[agent_run_service.py:351-383](../../../src/services/agent_run_service.py)
重复调用 `resolve_route + get_profile`。

**目标状态：**

```python
# src/services/image_input_service.py 新增方法
class ImageInputService:
    def build_vision_image_parts(
        self,
        *,
        llm_config: LLMConfig,
        images: list[str],
        model_override: str | None,
        llm_override: str | None,
        default_profile_key: str | None,
    ) -> tuple[dict[str, Any], ...]:
        """校验图片 + 检查 vision profile + 构造 image_parts。

        合并 ensure_vision_supported 与原 service 层的 image_parts 构造。
        """
        validated = self.validate_current_images(files=None, images=images)
        profile = self.ensure_vision_supported(
            llm_config=llm_config,
            llm_override=llm_override, model_override=model_override,
            default_profile_key=default_profile_key,
        )
        parts: list[dict[str, Any]] = []
        for item in validated:
            part: dict[str, Any] = {'url': item.url}
            if profile.vision_detail is not None:
                part['detail'] = profile.vision_detail
            parts.append(part)
        return tuple(parts)
```

`ensure_vision_supported` 保持公开（其他路径仍可能调用），但
`build_vision_image_parts` 是 service 层唯一应该调用的入口。

### 7.2 ContextAssembler 与 turn 装配下沉到 Exp

**当前状态：**`Exp.build_runtime`（[exp.py:479](../../../matmaster/core/exp.py)）已构造
`ContextAssembler` 实例并通过 `spec.context_assembler` 暴露给 kernel。但
service 层（Stage 5b）又自己构造一份用来做 turn 渲染。

**目标状态：**

`Exp.run_stream` 接管 turn 渲染：

```python
# matmaster/core/exp.py
async def run_stream(
    self,
    ctx: PlaygroundContext,
    *,
    cancel_token: CancellationToken | None = None,
    spawn_id: str | None = None,
) -> AsyncIterator[BusEvent]:
    """build_runtime -> turn 渲染 + 持久化 -> kernel.run_stream -> cleanup."""
    try:
        runtime = await self.build_runtime(ctx, spawn_id=spawn_id)
        spec = runtime.spec

        # Turn 渲染 + intent 解析 + 持久化（root run only）
        if spawn_id is None and spec.context_assembler is not None:
            turn_result = await self._render_and_persist_turn(
                ctx=ctx,
                context_assembler=spec.context_assembler,
                session_events_port=spec.session_events_port,
            )
            task = turn_result.user_prompt
            # active_skills 通过 ctx.metadata 注入到下游 _init_skill_tools 已读取
            if turn_result.active_skills:
                ctx = ctx.with_metadata(active_skills=turn_result.active_skills)
                # 注意 build_runtime 已完成，这里改 ctx 不会回流；
                # active_skills 已在 ContextAssembler 内部用于 SessionContextBuilder
        else:
            # spawn 路径：task 由 caller 通过 turn_input.user_text 传入
            task = ctx.metadata.turn_input.user_text if ctx.metadata.turn_input else ""

        if ctx.session is not None:
            ctx.session._cancel_token = cancel_token

        catalog = getattr(spec, "tool_catalog", None)
        if cancel_token is not None and catalog is not None:
            catalog.inject_cancel_token(cancel_token)

        async for event in runtime.kernel.run_stream(
            spec, task, history=None, cancel_token=cancel_token,
        ):
            yield event
    finally:
        await self._run_cleanup_callbacks()

async def _render_and_persist_turn(
    self,
    *,
    ctx: PlaygroundContext,
    context_assembler: ContextAssembler,
    session_events_port: SessionEventsPort,
) -> "TurnRenderResult":
    """assemble_turn + write_user_turn_context_event。"""
    instructions = ctx.metadata.user_instructions or UserInstructions(
        text="", hash=_hash_user_instructions(""), truncated=False,
    )
    turn_input = ctx.metadata.turn_input
    if turn_input is None:
        raise RuntimeError("turn_input missing in ctx.metadata; service must populate it")

    intent = await context_assembler.resolve_turn_intent(
        instructions_hash=instructions.hash,
        session_id=ctx.session_id,
        spawn_id=None,
        events_port=session_events_port,
    )
    assembly = await context_assembler.assemble_turn(
        intent=intent,
        request=TurnAssemblyRequest(
            session_id=ctx.session_id,
            spawn_id=None,
            turn_input=turn_input,
            user_instructions=instructions,
        ),
    )
    rendered = assembly.user_turn_context.to_message(ContextView.RUNTIME)

    writer_port = ctx.runtime_ports.user_turn_context_writer
    if writer_port is not None and writer_port.write is not None:
        await writer_port.write(
            payload={
                "schema_version": USER_TURN_CONTEXT_SCHEMA_VERSION,
                "kind": "anchor" if intent.is_anchor_turn else "continuation",
                "message": rendered.model_dump(mode="json"),
                "user_instructions_hash": (
                    instructions.hash if intent.is_anchor_turn else None
                ),
                "transform": DEFAULT_TURN_TRANSFORM,
                "render_version": USER_CONTEXT_RENDER_VERSION,
            },
        )

    return TurnRenderResult(
        user_prompt=rendered.content,
        active_skills=assembly.active_skills,
    )
```

### 7.3 ContextAssembler 增强

**当前 `AssemblyResult`：**仅含 `user_turn_context`、`user_instructions_text`、
`user_instructions_hash`、`used_composition`、`covered_until_event_id`。

**新增字段：**`active_skills: frozenset[str]`。`ContextAssembler.assemble_turn` 在已经读取
session events 的路径上一并产出（与 SessionContextBuilder 共享同一份 events）。

**新增方法：**`ContextAssembler.resolve_turn_intent(*, instructions_hash, session_id, spawn_id, events_port)`，
内容直接迁移自 [src/services/context_turn_intent.py](../../../src/services/context_turn_intent.py)。
迁移后 `src/services/context_turn_intent.py` 删除。

### 7.4 Skill resolver 装配下沉

**当前状态：**service 层 `_build_skill_resolver`
（[agent_run_service.py:143-180](../../../src/services/agent_run_service.py)）。

**目标状态：**

```python
# matmaster/core/exp.py 修改
async def build_runtime(
    self, ctx: PlaygroundContext,
    *, spawn_id: str | None = None,
) -> AgentRuntime:
    spec = await self.assemble(ctx)
    self._skill_registry = None

    registry = ToolRegistry()
    # ... 既有 builtin tools 装配 ...

    if skills_or_enabled:
        self._init_skill_tools(ctx, registry, catalog=catalog)
        # self._skill_registry 已被 _init_skill_tools 赋值

    # Skill resolver 在此构造，service 层不再传入
    self._skill_resolver = (
        SkillRegistryResolver(self._skill_registry)
        if self._skill_registry is not None
        else empty_skill_resolver
    )

    # ... 后续装配照旧 ...
```

`Exp.run_stream` 删除 `skill_resolver` 与 `skills` 参数。`_make_spawn_fn` 内部传递的
`skill_resolver` 改读 `self._skill_resolver`。

### 7.5 热缓存删除

直接删除：

- `AgentRunService._active_skills` 字段
- `AgentRunService._resolve_active_skill_names` 方法
- `AgentRunService.run_agent` 中 `_remember_skill_hit` 闭包及其在 Stage 6 中的调用
- Stage 5b 中 `pg_ctx.with_metadata(active_skills=...)` 的写入（改由 7.2 中的
  `_render_and_persist_turn` 通过 `ctx.with_metadata(active_skills=...)` 在 Exp 内部完成）

### 7.6 Figure 协调

```python
# src/services/figure_coordinator.py（新文件）
class FigureCoordinator:
    """Figure 累积 + 派发协调器。

    封装原 agent_run_service.py 中的 figure_accumulator + figure_dispatch_lock
    + 三个闭包（_dispatch_response_figures_if_dirty_unlocked、
    _record_tool_result_figures_and_dispatch_if_dirty、_child_event_sink）。

    持有对 fanout 的引用，对外暴露窄接口：
      - flush_if_dirty(reason)
      - record_tool_result(event, include_spawned, reason)
      - child_event_sink: 提供给 history wiring 用，作为子 agent 事件回流入口
      - upload_config: 提供给 RuntimePorts.figure_upload 用
    """

    def __init__(self, *, fanout: RunEventFanout, session_id: str, task_id: str) -> None:
        self._fanout = fanout
        self._accumulator = ResponseFiguresAccumulator()
        self._lock = asyncio.Lock()
        self._upload_config = _build_figure_upload_config(
            session_id=session_id, task_id=task_id,
        )

    @property
    def upload_config(self) -> FigureUploadConfig:
        return self._upload_config

    async def child_event_sink(self, event: BusEvent) -> None:
        try:
            await self._fanout.dispatch(event)
            if isinstance(event, ToolResultEvent):
                await self.record_tool_result(
                    event, include_spawned=True, reason='child_tool_result',
                )
        except Exception:
            logger.warning(
                'child event sink failed type=%s',
                getattr(event, 'type', '?'), exc_info=True,
            )

    async def flush_if_dirty(self, reason: str) -> None:
        async with self._lock:
            await self._dispatch_if_dirty_unlocked(reason)

    async def record_tool_result(
        self, event: ToolResultEvent, *, include_spawned: bool, reason: str,
    ) -> None:
        async with self._lock:
            self._accumulator.add_tool_result(event, include_spawned=include_spawned)
            await self._dispatch_if_dirty_unlocked(reason)

    async def _dispatch_if_dirty_unlocked(self, reason: str) -> None:
        snapshot = self._accumulator.build_snapshot_event_if_dirty()
        if snapshot is None:
            return
        try:
            await self._fanout.flush_persistence_barrier()
            dispatched = await self._fanout.dispatch_and_wait_persistence(snapshot)
        except Exception:
            logger.warning('response_figures dispatch failed reason=%s', reason, exc_info=True)
            return
        if dispatched:
            self._accumulator.mark_snapshot_emitted()
        else:
            logger.warning('response_figures dispatch reported handler failure reason=%s', reason)
```

`_build_figure_upload_config` 从 `agent_run_bohrium_stage.py` 提到此文件（或 figure_coordinator
独立调用既有的 `_build_figure_upload_config`——选简单的）。

## 8. API 变更与新增

### 8.1 `Exp.run_stream` 签名

**当前：**
```python
async def run_stream(
    self, ctx, task,
    *, history=None, cancel_token=None,
    skills=None, skill_resolver=None, spawn_id=None,
) -> AsyncIterator[Any]
```

**目标：**
```python
async def run_stream(
    self, ctx: PlaygroundContext,
    *, cancel_token: CancellationToken | None = None,
    spawn_id: str | None = None,
) -> AsyncIterator[BusEvent]
```

破坏性变更。所有 caller（service 层 + `_make_spawn_fn` 内部）必须同步改：
- 不再传 `task`、`history`、`skills`、`skill_resolver`
- 必要数据通过 `ctx.metadata.turn_input`、`ctx.metadata.user_instructions` 传入
- `history` 改通过 `ctx.runtime_ports.compaction.history` 端口（已存在）

### 8.2 `ImageInputService.build_vision_image_parts`

详见 7.1。

### 8.3 `FigureCoordinator` 类

详见 7.6。

### 8.4 `PlaygroundRuntimePorts.user_turn_context_writer`

新增 port：

```python
# matmaster/types/runtime_ports.py
class UserTurnContextWritePayload(TypedDict):
    schema_version: str
    kind: str
    message: dict[str, Any]
    user_instructions_hash: str | None
    transform: str
    render_version: str


class UserTurnContextWriter(Protocol):
    async def __call__(self, *, payload: UserTurnContextWritePayload) -> None: ...


@dataclass(frozen=True)
class UserTurnContextWriterPort:
    write: UserTurnContextWriter | None = None


@dataclass(frozen=True)
class PlaygroundRuntimePorts:
    child_event_forward_sink: BusEventSink | None = None
    compaction: PlaygroundCompactionPort = field(default_factory=PlaygroundCompactionPort)
    figure_upload: FigureUploadPort = field(default_factory=FigureUploadPort)
    bohrium: BohriumRuntimePort = field(default_factory=BohriumRuntimePort)
    user_turn_context_writer: UserTurnContextWriterPort = field(
        default_factory=UserTurnContextWriterPort,
    )
```

service 层注入回调：

```python
# src/services/agent_run_service.py
def _make_user_turn_context_writer(
    self, events_table, session_id, task_id, invocation_id,
) -> UserTurnContextWriter:
    async def write(*, payload: UserTurnContextWritePayload) -> None:
        try:
            await write_user_turn_context_event(
                events_table=events_table,
                session_id=session_id, task_id=task_id,
                invocation_id=invocation_id, spawn_id=None,
                payload=payload,
            )
        except Exception as exc:
            logger.exception(
                "user_turn_context write failed; session_id=%s invocation_id=%s",
                session_id, invocation_id,
            )
            raise  # 让 Exp.run_stream 决定是否中止本 turn
    return write
```

### 8.5 `ContextAssembler.resolve_turn_intent` 与 `active_skills`

**新增方法：**
```python
# matmaster/context/assembly.py
class ContextAssembler:
    async def resolve_turn_intent(
        self,
        *,
        instructions_hash: str,
        session_id: str,
        spawn_id: str | None,
        events_port: SessionEventsPort,
    ) -> ContextAssemblyIntent:
        ...  # 迁自 src/services/context_turn_intent.py:resolve_turn_context_intent
```

**修改 `AssemblyResult`：**
```python
@dataclass(frozen=True)
class AssemblyResult:
    user_turn_context: UserTurnContext
    user_instructions_text: str
    user_instructions_hash: str
    used_composition: str
    covered_until_event_id: int | None = None
    active_skills: frozenset[str] = frozenset()  # 新增
```

`assemble_turn` 内部从 session_sections 构造时已经持有 events，可一并扫
`scan_skill_hits`，零额外成本。

## 9. 文件结构变更

**修改：**

- [src/services/agent_run_service.py](../../../src/services/agent_run_service.py) —
  缩到约 150 行，按 6.2 重写
- [matmaster/core/exp.py](../../../matmaster/core/exp.py) — `run_stream` 签名变更，
  新增 `_render_and_persist_turn`，`build_runtime` 内部构造 skill_resolver
- [matmaster/context/assembly.py](../../../matmaster/context/assembly.py) —
  `AssemblyResult` 加 `active_skills`，新增 `resolve_turn_intent` 方法
- [matmaster/types/runtime_ports.py](../../../matmaster/types/runtime_ports.py) —
  新增 `UserTurnContextWriterPort`
- [matmaster/core/runtime_context_assembly.py](../../../matmaster/core/runtime_context_assembly.py) —
  传 active_skills accumulator 给 ContextAssembler，无 API 变更
- [src/services/image_input_service.py](../../../src/services/image_input_service.py) —
  新增 `build_vision_image_parts`

**新建：**

- `src/services/figure_coordinator.py` — `FigureCoordinator` 类
- `tests/matmaster/services/test_agent_run_service_orchestration_boundary.py` —
  boundary tests
- `tests/services/test_figure_coordinator.py` — 单元测试
- `tests/matmaster/core/test_exp_turn_rendering.py` — Exp 内 turn 渲染测试

**删除（在 Exp 接管完成、boundary test 全绿后）：**

- [src/services/context_assembly_factory.py](../../../src/services/context_assembly_factory.py)
- [src/services/context_turn_intent.py](../../../src/services/context_turn_intent.py)
- [src/services/context_assembly_ports.py](../../../src/services/context_assembly_ports.py) —
  `AppSessionEventsPort` 的能力被 `build_history_wiring` 的 history port 覆盖；
  确认无其他 caller 后删除
- `AgentRunService._build_skill_resolver`、`AgentRunService._resolve_active_skill_names`、
  `AgentRunService._active_skills` 字段
- `AgentRunService` 中 figure 相关 5 个本地闭包

## 10. 数据流时序

### 10.1 当前流程（简化）

```
service.run_agent
  ├─ playground.prepare(dict)
  ├─ events_table = get_chat_events_table()
  ├─ fanout = RunEventFanout(SSE + Persistence)
  ├─ run_bohrium_stage → pg_ctx + user_instructions
  ├─ resolve_route + get_profile             ←─ 冗余 1
  ├─ ensure_vision_supported + image_parts 构造
  ├─ build_provider                          ← 自己又 resolve_route
  ├─ Exp(exp_config)
  ├─ figure_accumulator + lock + 3 闭包
  ├─ AskQuestionBridge
  ├─ build_history_wiring
  ├─ instructions_bundle + _build_skill_resolver  ←─ 冗余 3
  ├─ build_context_assembler                 ←─ 冗余 2
  ├─ resolve_turn_context_intent
  ├─ TurnInput 默认构造
  ├─ context_assembler.assemble_turn
  ├─ write_user_turn_context_event
  ├─ user_prompt = rendered.content
  ├─ _resolve_active_skill_names + 热缓存    ←─ 冗余 3 续
  ├─ pg_ctx.with_run_meta(active_skills=...)
  ├─ exp.run_stream(pg_ctx, user_prompt, history, skills, skill_resolver)
  │    └─ Exp.build_runtime
  │         └─ build_runtime_context_assembly  ← 又构造一份 ContextAssembler
  │    └─ kernel.run_stream
  ├─ async for event: dispatch + figure record
  ├─ post-processing
  └─ finally cleanup
```

### 10.2 目标流程

```
service.run_agent
  ├─ playground.prepare(RunMetadata(...), session_id=...)
  ├─ events_table = get_chat_events_table()
  ├─ fanout = RunEventFanout
  ├─ figure_coordinator = FigureCoordinator(fanout, ...)
  ├─ bridge = build_interaction_bridge(...)
  ├─ run_bohrium_stage → pg_ctx + user_instructions
  ├─ build_provider(llm_config, *overrides)
  ├─ image_parts = image_service.build_vision_image_parts(...)
  ├─ pg_ctx = pg_ctx.model_copy(update={'llm_provider': ..., 'interaction_bridge': bridge})
  ├─ build_history_wiring(base_runtime_ports=pg_ctx.runtime_ports, ...)
  ├─ pg_ctx.with_runtime_port(
  │      figure_upload=FigureUploadPort(config=figure_coordinator.upload_config),
  │      user_turn_context_writer=UserTurnContextWriterPort(write=...),
  │  )
  ├─ pg_ctx.with_metadata(
  │      turn_input=TurnInput(...),
  │      user_instructions=instructions_bundle,
  │  )
  ├─ exp = Exp(exp_config)
  ├─ async with aclosing(exp.run_stream(pg_ctx, cancel_token=cancel_token)) as stream:
  │    └─ Exp.run_stream
  │         ├─ build_runtime
  │         │    ├─ _init_skill_tools → skill_registry
  │         │    ├─ skill_resolver = SkillRegistryResolver(skill_registry)
  │         │    └─ build_runtime_context_assembly → context_assembler
  │         ├─ _render_and_persist_turn(ctx, context_assembler, events_port)
  │         │    ├─ resolve_turn_intent
  │         │    ├─ assemble_turn → AssemblyResult(active_skills=..., ...)
  │         │    └─ ctx.runtime_ports.user_turn_context_writer.write(payload=...)
  │         ├─ ctx = ctx.with_metadata(active_skills=...)
  │         └─ kernel.run_stream(spec, task=rendered.content, ...)
  ├─ async for event: dispatch + figure_coordinator.record_tool_result
  ├─ _finalize_run
  └─ _cleanup_run
```

关键差异：

- 路由 / vision / image_parts 装配集中在 image_service 与 build_provider，service 层零重复
- ContextAssembler 只在 Exp 内部构造一份
- skill_resolver 装配完全在 Exp 内
- turn 渲染、intent 解析、user_turn_context 持久化全部在 Exp 内
- active_skills 从 assemble_turn 一并产出，service 层无热缓存
- figure 协调封装到类

## 11. 测试策略

### 11.1 Boundary 测试

`tests/matmaster/services/test_agent_run_service_orchestration_boundary.py`：

```python
def test_agent_run_service_does_not_import_context_assembler():
    """service 层禁止 import ContextAssembler 相关装配。"""
    import ast
    src = Path("src/services/agent_run_service.py").read_text()
    tree = ast.parse(src)
    imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    forbidden = {
        "matmaster.context.assembly",
        "src.services.context_assembly_factory",
        "src.services.context_turn_intent",
        "src.services.user_turn_context_service",
    }
    leaked = forbidden & {i for i in imports if i}
    assert not leaked, f"agent_run_service.py 仍在 import 已下沉模块: {leaked}"


def test_agent_run_service_does_not_construct_skill_resolver():
    """service 层禁止构造 SkillResolver。"""
    src = Path("src/services/agent_run_service.py").read_text()
    assert "SkillRegistryResolver" not in src
    assert "_build_skill_resolver" not in src


def test_agent_run_service_does_not_hold_active_skills_cache():
    """删除 _active_skills 热缓存后必须保持删除。"""
    from src.services.agent_run_service import AgentRunService
    svc = AgentRunService()
    assert not hasattr(svc, "_active_skills")


def test_agent_run_service_does_not_call_resolve_route_or_get_profile():
    """LLM 路由解析必须下沉到 build_provider / image_service。"""
    src = Path("src/services/agent_run_service.py").read_text()
    assert "resolve_route" not in src
    assert "get_profile" not in src
```

### 11.2 单元测试

- `tests/services/test_figure_coordinator.py`：
  - `child_event_sink` 在主流程外正确累积
  - `flush_if_dirty` 走 `flush_persistence_barrier + dispatch_and_wait_persistence`
  - 并发 `record_tool_result` 调用走锁
  - dispatch 失败后 `mark_snapshot_emitted` 不被调用
- `tests/matmaster/types/test_runtime_ports.py`：
  - `UserTurnContextWriterPort` frozen + 默认 `write=None`
- `tests/matmaster/context/test_assembly.py`：
  - `AssemblyResult.active_skills` 字段从 events 正确扫描
  - `resolve_turn_intent` 与原 `resolve_turn_context_intent` 行为一致
- `tests/matmaster/services/test_image_input_service.py`：
  - `build_vision_image_parts` 校验 + vision profile + image_parts 一次产出
  - 校验失败正确抛 `ImageInputError`

### 11.3 集成测试

- `tests/matmaster/core/test_exp_turn_rendering.py`：
  - Exp.run_stream 通过 `user_turn_context_writer` port 写入 payload
  - 缺 `turn_input` 时抛 RuntimeError
  - spawn 路径不走 turn 渲染（spawn_id 非 None）
  - `ctx.metadata.user_instructions` 是 None 时使用 fallback bundle
  - 不重算 instructions hash
- `tests/matmaster/integration/test_image_input_e2e.py`（既有）：
  - 必须仍然绿，验证 image_parts 通过 TurnInput.images 一路到 kernel
- `tests/matmaster/services/test_agent_run_stream.py`：
  - run_agent 减少调用数后端到端依然正确

### 11.4 已有测试需要改写

- 与 `_build_skill_resolver`、`_resolve_active_skill_names`、`_active_skills` 相关的全部
  service 层测试删除或重写
- 与 `build_context_assembler`、`resolve_turn_context_intent` 相关的 service 层测试
  迁到 `matmaster/context/test_assembly.py`
- `test_run_agent_*` 中调 `exp.run_stream(task, ...)` 的所有 mock 需要改 mock 签名

## 12. 迁移路径

### 12.1 前置依赖

`run-meta-refactor` P0 + P1 + P2 全部 merge。本设计假定：

- `pg_ctx.metadata` 已是 `RunMetadata` typed
- `pg_ctx.session_id` 顶层
- `PlaygroundRuntimePorts` 已含 figure_upload、bohrium 子 port
- `Playground.prepare(metadata: RunMetadata, *, session_id, ...)` 强制 typed
- `AgentRuntimeSpec.run_identity + turn_input` 已替代 meta dict
- `exp.run_stream(skills=...)` 参数已删除（P1 Task 7）

### 12.2 阶段切分

**O1：服务层装配清理（低风险，独立 PR）**

可与 O2-O4 解耦：

- 引入 `ImageInputService.build_vision_image_parts`
- 删除 service Stage 4 中的 `resolve_route` / `get_profile` 重复调用
- 引入 `FigureCoordinator` 类，替换原闭包堆
- 引入 `build_interaction_bridge` 小工厂
- 加 boundary test：service 层不再调 `resolve_route` / `get_profile`

**O2：active_skills 路径下沉（中风险，独立 PR）**

依赖 O1 不强；可与 O1 并行：

- `AssemblyResult.active_skills` 新增字段
- `ContextAssembler.assemble_turn` 内部产出
- service 层删除 `_resolve_active_skill_names`、`_remember_skill_hit`、`_active_skills`
- Stage 5b 收尾 `pg_ctx.with_metadata(active_skills=...)` 改由 ContextAssembler 调用方写入

**O3：skill_resolver 装配下沉（中风险，依赖 O2 完成）**

- `Exp.build_runtime` 内部构造 `SkillRegistryResolver`
- `Exp.run_stream` 删除 `skill_resolver` 参数
- `_make_spawn_fn` 改读 `self._skill_resolver`
- service 层删除 `_build_skill_resolver`，删除对 `skill_registry_factory`、`skill_resolver`
  的 import
- boundary test 加 `SkillRegistryResolver not in source`

**O4：turn 渲染下沉到 Exp（高风险，最大改动）**

依赖 O3 完成（Exp 已经能内部产出 `skill_resolver`，否则 turn 渲染没法在 Exp 内部用到正确
resolver）。

- 新增 `UserTurnContextWriterPort`
- `ContextAssembler.resolve_turn_intent` 新方法
- `Exp.run_stream` 新签名 + `_render_and_persist_turn` 方法
- service 层删除整个 Stage 5b
- service 层注入 `UserTurnContextWriterPort` via `with_runtime_port`
- 删除 `src/services/context_assembly_factory.py`、`src/services/context_turn_intent.py`、
  `src/services/context_assembly_ports.py`（如确认无其他 caller）
- `tests/matmaster/integration/test_image_input_e2e.py` 长期常驻

每个 O 阶段独立 PR。O1-O2 可并行，O3 等 O2 merge，O4 等 O3 merge。

## 13. 风险与开放问题

### 13.1 风险

**R1：`AppSessionEventsPort` 删除后的 events 端口归一**

[src/services/context_assembly_ports.py:AppSessionEventsPort](../../../src/services/context_assembly_ports.py)
当前与 `build_history_wiring` 输出的 history port 同时存在。前者用于 service 层
`build_context_assembler` 直接读 events_table，后者通过 runtime_ports 注入。两者数据源
相同但实现不同。O4 后 service 层不再直接构造 ContextAssembler，需要确认 Exp 内
`build_runtime_context_assembly` 用的是 history port 即可覆盖所有读取，再删除
`AppSessionEventsPort`。

**R2：spawn 路径的 turn 渲染**

子 Exp 通过 `_make_spawn_fn` 调用，spawn_id 非 None。当前 spawn 路径不走 turn 渲染（spawn
agent 不写 user_turn_context_event），需要 `_render_and_persist_turn` 内部明确检查
`spawn_id is None`，否则会污染 root agent 的 user_turn_context 序列。已在 7.2 的伪代码中
体现。

**R3：write 失败的处理时机**

当前 service 层 Stage 5b 在 `write_user_turn_context_event` 失败时直接 return 失败结果。
下沉到 Exp 后，需要决定：

- 方案 A：write 失败抛异常，由 `Exp.run_stream` 的外层 try/except 处理，service 层在
  Stage 6 主循环外的异常分支收到
- 方案 B：Exp.run_stream 内部捕获并 yield 一个特殊 ErrorEvent，service 层正常处理

推荐方案 A，因为 user_turn_context 是 turn 的硬一致性约束，硬抛比 yield event 更直观。

**R4：HistoryCheckpointService 与 checkpoint_sink_factory 的归属**

当前 service 层构造 `HistoryCheckpointService` 然后通过 `_checkpoint_sink_factory` 闭包
传给 `build_history_wiring`。`HistoryCheckpointService` 本身依赖 `events_table`，属于
持久化能力，归属 service 层合理。但闭包内同时引用了 fanout、session_id、task_id、
invocation_id，造成 service 持有较多状态。

本设计不动 checkpoint 路径，保持现状。后续可考虑提取
`build_checkpoint_sink_factory(checkpoint_service, fanout, ...)` 小工厂，但本设计不强求。

### 13.2 开放问题

**Q1：service 层 `_make_user_turn_context_writer` 闭包持有 4 个 caller-scope 变量**

`events_table` / `session_id` / `task_id` / `invocation_id` 四个变量在闭包内引用。
是否应该提到一个 `UserTurnContextWriterFactory` class？

提议：本设计先保留闭包形式（保持轻量）。若闭包数量增加再考虑提取。

**Q2：`ContextAssembler` 是否应该直接接收 `user_turn_context_writer`，由它自己调？**

提议：不。`ContextAssembler` 是核心层 typed model，不应感知"写事件"这种持久化语义。
写入由 `Exp._render_and_persist_turn` 编排，`ContextAssembler` 只负责装配。

**Q3：`active_skills` 从 ContextAssembler 产出 vs 由 SessionContextBuilder 产出？**

`SessionContextBuilder.build_sections` 内部已经通过 `skill_resolver(events)` 产出
`active_skills`（[runtime_context_assembly.py:52-58](../../../matmaster/core/runtime_context_assembly.py)）。
`AssemblyResult.active_skills` 应当从这里透传，而非重新扫描。

需要在 SessionContextBuilder 暴露已计算的 active_skills，或让 `_session_section_builder`
返回 sections + active_skills 元组。本设计倾向后者：

```python
SessionSectionBuilder = Callable[
    [tuple[SessionEvent, ...], int, bool],
    tuple[tuple[ContextSection, ...], frozenset[str]],  # 多一个 active_skills
]
```

需要同步更新 `_no_session_sections` 返回 `((), frozenset())`，以及
`_build_via_factory` 从 builder 读取 active_skills。

**Q4：`Exp.run_stream` 的 `task` 字段消失后，devshell / evaluation 路径如何兼容？**

[matmaster/devshell/runner.py](../../../matmaster/devshell/runner.py) 与
[evaluation/core/mat_runner.py](../../../evaluation/core/mat_runner.py) 当前
直接调 `exp.run_stream(ctx, task, ...)`。这两个路径不走 user_turn_context_event 持久化
（无 events_table 概念）。

提议：保留 `task` 入参的兼容入口形态——但与 run-meta-refactor 的"不留兼容"原则冲突。
两个选项：

- 选项 A：devshell / evaluation 也改为构造 `TurnInput` 写入 `ctx.metadata`，然后调
  `exp.run_stream(ctx)` 无入参。`_render_and_persist_turn` 检查
  `ctx.runtime_ports.user_turn_context_writer.write is None` 时跳过持久化但仍渲染 prompt。
- 选项 B：保留 `task` 作为可选 fallback 参数：当传入时直接当作 user_prompt，跳过
  turn 渲染（devshell / evaluation 路径不需要 anchor/continuation 协议）。

推荐选项 A，与 run-meta-refactor 风格一致。devshell / evaluation 改动量约 5-10 行。
