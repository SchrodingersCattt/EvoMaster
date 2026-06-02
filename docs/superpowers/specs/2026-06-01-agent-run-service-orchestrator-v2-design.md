# AgentRunService 编排器 V2 收敛设计

- Date: 2026-06-01
- Status: Draft
- Author: Kealdoom + Codex
- Supersedes:
  - `docs/superpowers/specs/2026-05-18-agent-run-service-orchestrator-design.md`
- 基线:
  - 当前 checkout: `refactor/context`
  - 最近提交: `e34b6bb4 merge: resolve origin/main conflicts`
- 影响范围:
  - `src/services/agent_run_service.py`
  - `src/services/agent_run_history_wiring.py`
  - `src/services/image_input_service.py`
  - `src/services/response_figures_service.py`
  - `src/services/skill_registry_factory.py`
  - `src/services/skill_resolver.py`
  - `src/services/context_assembly_factory.py`
  - `src/services/context_turn_intent.py`
  - `src/services/context_assembly_ports.py`
  - `matmaster/core/exp.py`
  - `matmaster/core/run_context.py`
  - `matmaster/core/runtime_context_assembly.py`
  - `matmaster/context/assembly.py`
  - `matmaster/context/session.py`
  - `matmaster/types/runtime.py`
  - `matmaster/types/runtime_ports.py`
  - tests under `tests/matmaster/services/`, `tests/matmaster/core/`,
    `tests/matmaster/context/`, `tests/matmaster/types/`

## 1. 背景

旧版 `2026-05-18-agent-run-service-orchestrator-design.md` 的核心判断仍然成立：
`AgentRunService` 承担了过多运行期装配职责，应收敛为生产入口编排层。

但旧版文档的 API 假设已经过时。当前代码已经完成新的运行边界拆分：

- `ExecutionEnvironment` 位于 `matmaster/core/playground.py`，只表达物理执行基底：
  workspace、session、cache、归档配置、`RunMetadata`、Bohrium snapshot。
- `AgentRunRequest` 位于 `matmaster/core/run_context.py`，表达每轮由 service 层解析出来的
  runtime ingredients：LLM provider/config、turn input、user instructions、active skills、
  interaction bridge、runtime capability ports。
- `AgentRunContext` 是 `ExecutionEnvironment + AgentRunRequest` 的组合，也是 `Exp`
  的单一入口对象。
- `AgentRunPorts` 位于 `matmaster/types/runtime_ports.py`，承载 service 注入给 runtime
  的窄能力端口。
- `AgentKernelSpec + AgentKernelResources + AgentKernelRuntime` 已替代旧
  `AgentRuntimeSpec`。kernel-facing spec 只保留配置和身份；live resources 放在 resources。

因此，本 V2 spec 不再以 `PlaygroundContext`、`PlaygroundRuntimePorts`、
`AgentRuntimeSpec` 为设计中心，而是基于当前真实边界重新设计。

## 2. 当前事实

### 2.1 `AgentRunService` 仍然是大编排器

当前 `src/services/agent_run_service.py` 约 856 行。`AgentRunService.run_agent`
仍承担以下职责：

- 准备 `ExecutionEnvironment`
- 构造 `RunEventFanout`
- 调用 `run_bohrium_stage`
- 加载 exp config 与 LLM config
- 校验 vision capability，并构造 `TurnInput.image_detail`
- 构造 `Exp`
- 构造 `HistoryCheckpointService`
- 维护 response figures 的 accumulator、lock、多个本地闭包
- 构造 `AskQuestionBridge`
- 构造 history wiring 与 compaction port
- 构造 `SkillRegistryResolver`
- 构造 service 层 `ContextAssembler`
- 解析 root turn 的 anchor / continuation intent
- 渲染并持久化 `user_turn_context`
- 扫描并缓存 active skills
- 组装 `AgentRunContext`
- 驱动 `Exp.run_stream`
- 分发事件、处理 response figures、记录 `RunResultEvent`
- 执行 stream close、quota、Bohrium cleanup、fanout drain、Redis stop cleanup

这里的问题不是函数长本身，而是 service 同时拥有运行生命周期、持久化边界、context 装配、
skill resolver 与 figure 协调状态。后续维护任何一条链路都会牵动整个函数。

### 2.2 `ContextAssembler` 被构造两次

service 层仍通过 `src/services/context_assembly_factory.py` 构造一份 `ContextAssembler`，
用于当前 turn 渲染。

`Exp.build_runtime` 又通过 `matmaster/core/runtime_context_assembly.py` 构造另一份
`ContextAssembler`，用于 compaction 和 runtime context reassembly。

两者都依赖 session events 和同一个 skill resolver 概念，只是 port adapter 不同：

- service 层使用 `AppSessionEventsPort(events_table)`
- runtime 层使用 `ctx.request.ports.compaction.history`

当前 `build_history_wiring` 已经提供了符合 `SessionEventHistoryPort` 的 history port，
因此 service 层再构造 `AppSessionEventsPort` 是一个可删除的并行路径。

### 2.3 skill resolver 存在重复边界

当前 active-skill prompt rendering 依赖 service 层：

- `src/services/skill_registry_factory.py`
- `src/services/skill_resolver.py`
- `AgentRunService._build_skill_resolver`

而 `Exp._init_skill_tools` 内部也会构造 `matmaster.skills.registry.SkillRegistry`，
用于注册 `SkillTool` 与 lazy MCP tools。

旧版 spec 建议让 `Exp.build_runtime` 直接构造 `SkillRegistryResolver`。这个方向要保留，
但不能让 `matmaster/core/exp.py` 反向 import `src/services/skill_resolver.py`。
因此必须先把 resolver 逻辑提升到 `matmaster` 包内的合适位置。

### 2.4 `_active_skills` 热缓存收益低且风险高

当前 `AgentRunService.__init__` 持有：

```python
self._active_skills: dict[str, frozenset[str]] = {}
```

该缓存以 service 进程内状态为准。生产环境采用 API / Worker 分离、多实例 worker pool，
同一 session 后续 turn 不保证落到同一进程。缓存命中率依赖调度偶然性，且漏记
`SkillHitEvent` 会导致本地缓存与 DB 事实不一致。

权威来源应是持久化事件。`ContextAssembler` 在 anchor turn 读取 session events 时已经具备
同一批事件，active skills 应当从这条路径产出，而不是由 service 额外扫 DB 再热缓存。

### 2.5 image 输入契约已经从 image_parts 转为 `TurnInput`

旧版 spec 设计 `ImageInputService.build_vision_image_parts(...)` 返回 provider image parts。
当前真实契约已经改变：

- `TurnInput.attachments.images` 保存 URL
- `TurnInput.attachments.image_detail` 保存 `low` / `high` / `auto`
- kernel 内部通过 `images_as_parts()` 生成 `ImageContentPart`

因此 V2 不再引入 `image_parts` helper，而是引入当前契约下的 image detail / turn input enrichment
helper。

## 3. 目标

1. 让 `AgentRunService` 回到生产编排职责：
   - 准备物理环境
   - 构造 fanout 与持久化 handler
   - 调用 Bohrium stage
   - 组装 `AgentRunRequest`
   - 驱动 `Exp.run_stream`
   - 做 terminal event、quota、cleanup

2. 删除 service 层的重复 context 装配：
   - `AgentRunService` 不再 import `ContextAssembler`
   - `AgentRunService` 不再 import `build_context_assembler`
   - `AgentRunService` 不再 import `resolve_turn_context_intent`
   - `AgentRunService` 不再直接调用 `write_user_turn_context_event`

3. 让 root turn 渲染归 `Exp` 内部编排，但不暴露给 kernel-facing runtime：
   - `AgentKernelSpec` 不新增 `context_assembler`
   - `AgentKernelResources` 不新增 `context_assembler`
   - `ContextAssemblyRuntime` 只留在 `AgentRuntime` 或 `Exp` 内部私有 lifecycle 中

4. 删除 `_active_skills` 热缓存：
   - active skills 从 root pre-runtime 的 `resolve_turn_intent` 事件扫描产出（§7.3）
   - `AgentRunRequest.active_skills` 仍作为 runtime 输入快照存在
   - `Exp._init_skill_tools` 仍从 `ctx.request.active_skills` replay lazy MCP tools

5. 把 response figures 协调逻辑从 `run_agent` 本地闭包提取成 service 层对象。

6. 保持 API / Worker 分离约束：
   - 不依赖处理 HTTP 请求的进程与执行 agent 的 worker 是同一进程
   - 所有跨进程事实仍通过 Redis、DB、event stream 或 session events 表达

## 4. 非目标

- 不改 SSE / Redis / DB 事件协议。
- 不重写 `AgentKernel.run_stream` 主循环。
- 不把 `AgentRunContext` 直接传给 `AgentKernel`。
- 不复活 `AgentRuntimeSpec`。
- 不把 `ContextAssembler` 暴露到 `AgentKernelSpec` 或 `AgentKernelResources`。
- 不引入新的依赖注入框架。
- 不把 `src/services` 反向 import 到 `matmaster/core`。
- 不重做 Bohrium 提交、SSH attach、workspace upload、remote workdir 校验。
- 不更改 AskQuestion 协议，只整理 bridge 装配位置。
- 不把 user_turn_context 写入降级为 best effort。写入失败仍应中止当前 turn。

## 5. 目标分层

```
src/services/agent_run_service.py
  生产入口编排
  - environment / fanout / Bohrium / history wiring
  - 组装 AgentRunRequest
  - 驱动 Exp.run_stream
  - terminal handling / quota / cleanup

matmaster/core/exp.py
  runtime 装配与 root turn 准备
  - build_runtime 构造 tools / kernel runtime / context runtime
  - root turn rendering + user_turn_context writer port
  - child run factory
  - cleanup lifecycle

matmaster/context/turn_intent.py
  自由函数 resolve_turn_intent(events_port, ...)
  - 事件扫描得到 turn intent 与 active skill names
matmaster/context/assembly.py
  context assembly 纯领域逻辑
  - assemble turn / compaction

matmaster/types/runtime_ports.py
  窄能力端口
  - AgentRunPorts service -> Exp/runtime
  - KernelRuntimePorts Exp -> kernel
```

## 6. 架构决策

| # | 决策 | 说明 |
|---|---|---|
| D1 | 保留 `AgentRunContext` 作为 `Exp` 的入口 | 当前 `ExecutionEnvironment + AgentRunRequest` 分层已经清晰，不再回到 `PlaygroundContext`。 |
| D2 | user_turn_context writer 归入 `AgentRunPorts` | 当前能力端口已经由 `AgentRunRequest.ports` 承载，不新增 `PlaygroundRuntimePorts`。 |
| D3 | `Exp` 接管 root turn rendering | service 不再直接构造 `ContextAssembler`、解析 intent、持久化 user_turn_context。 |
| D4 | context runtime 不暴露给 kernel-facing runtime | `AgentKernelSpec` 与 `AgentKernelResources` 继续保持不含 assembly internals。 |
| D5 | active skills 解析早于 `build_runtime`，turn 渲染在其后 | active skills 影响 lazy MCP replay，必须前置；但只需要一次 `skill_hit` 事件扫描加小窗口 intent 判断，不需整轮渲染。渲染复用 `build_runtime` 已造的 assembler，因此无需 prebuilt 穿线。 |
| D6 | active skill names 从 root pre-runtime 事件扫描产出 | `resolve_turn_intent` 是给 `_init_skill_tools` 用的名字集唯一来源；`ContextAssembler` 只在 prompt 组装内部用 `skill_resolver(events)` 生成 `ActiveSkill` DTO，不向外返回名字集。 |
| D7 | skill resolver 先提升到 `matmaster` 包内 | `Exp` 不能 import `src/services/skill_resolver.py`，resolver 逻辑必须移动。 |
| D8 | image helper 返回 `TurnInput` enrichment，而不是 provider image parts | 当前图片输入契约是 `TurnInput.images + image_detail`。 |
| D9 | `FigureCoordinator` 放在 `src/services/figure_coordinator.py` | 它依赖 `RunEventFanout`、persistence barrier 和 service fanout 语义，属于 service 层基础设施。 |
| D10 | `run_agent` 不强制压到 150 行 | 目标是职责边界清晰。实际函数可接受 250 到 350 行，避免为了行数制造过多间接层。 |

## 7. 新增与修改的核心类型

### 7.1 `AgentRunPorts.user_turn_context_writer`

新增写入端口。它是 service 注入给 `Exp` 的持久化能力。

```python
@dataclass(frozen=True)
class UserTurnContextWriteRequest:
    session_id: str
    task_id: str | None
    invocation_id: str | None
    spawn_id: str | None
    kind: Literal["anchor", "continuation"]
    message: Message
    user_instructions_hash: str | None
    transform: str
    render_version: str
    schema_version: str


@runtime_checkable
class UserTurnContextWriter(Protocol):
    async def __call__(self, request: UserTurnContextWriteRequest) -> None: ...


@dataclass(frozen=True)
class AgentRunPorts:
    child_event_forward_sink: BusEventSink | None = None
    compaction: PlaygroundCompactionPort = field(default_factory=PlaygroundCompactionPort)
    figure_upload: FigureUploadPort = field(default_factory=FigureUploadPort)
    interrupt_checker: InterruptChecker | None = None
    user_turn_context_writer: UserTurnContextWriter | None = None
```

说明：

- port 传 typed request，不传 `dict[str, Any]`。
- service writer 内部再把 typed request 转换成 `write_user_turn_context_event` 需要的 payload。
- writer 失败必须抛异常，由 `Exp.run_stream` 外层传播到 `AgentRunService` 异常分支。

### 7.2 `AgentRuntime.context_runtime`

当前 `AgentRuntime` 只包含：

```python
@dataclass(frozen=True)
class AgentRuntime:
    kernel: Any
    kernel_runtime: AgentKernelRuntime
    cleanup: Callable[[], Any]
```

新增非 kernel-facing 字段：

```python
@dataclass(frozen=True)
class AgentRuntime:
    kernel: Any
    kernel_runtime: AgentKernelRuntime
    cleanup: Callable[[], Any]
    context_runtime: ContextAssemblyRuntime | None = None
```

约束：

- `AgentKernelSpec` 不新增字段。
- `AgentKernelResources` 不新增 context assembly 字段。
- `context_runtime` 由 `Exp.build_runtime` 内部构造，并作为 `AgentRuntime.context_runtime`
  挂到返回值上。
- root pre-runtime 不构造 `ContextAssemblyRuntime`，也不把 prebuilt runtime 传回
  `build_runtime`；渲染阶段只复用 `runtime_scope` 已经返回的那一份。
- devshell 若直接使用 `runtime_scope` 驱动 kernel，可以忽略该字段。

### 7.3 `resolve_turn_intent`（自由函数）

把 `src/services/context_turn_intent.py` 的逻辑迁入 core context 层，迁移目标
`matmaster/context/turn_intent.py`。保持自由函数形态，只吃 `events_port`，不依赖
`ContextAssembler` / `SkillRegistry` / `skill_resolver` 实例：

```python
@dataclass(frozen=True)
class TurnIntentResolution:
    intent: ContextAssemblyIntent
    active_skills: frozenset[str] = frozenset()


async def resolve_turn_intent(
    *,
    events_port: SessionEventHistoryPort,
    instructions_hash: str,
    session_id: str,
    spawn_id: str | None,
    active_skill_event_limit: int = DEFAULT_ACTIVE_SKILL_EVENT_LIMIT,
) -> TurnIntentResolution:
    """读 intent 相关事件与 skill_hit 事件，返回本轮 intent 与 active skill names。"""
    ...
```

函数内部做两类读取，不能混成同一个最近事件窗口：

- intent 解析读取较小窗口的 `user_turn_context` 与 `history_checkpoint`，调用
  `decide_turn_context_intent` 得到本轮 intent。
- active skill names 读取单独窗口的 `skill_hit` 事件，再用 `scan_skill_hits` 纯内存扫描得到。
  这个窗口必须覆盖运行期需要 replay 的历史范围，不能被 intent 的小窗口截断。默认值放在
  `matmaster/context/turn_intent.py`，后续若要与 service 的 `_DIALOG_HISTORY_MAX_EVENTS`
  对齐，也只通过参数传入，不从 core 反向 import service。

保持自由函数而非 `ContextAssembler` 方法是关键决策：它让 `Exp` 的 root pre-runtime
阶段只用一个 `events_port` 就能拿到 intent 与 active skills，**无需先构造 assembler**。
这是后续 §8.2 能砍掉 prebuilt 穿线的前提。`active_skills` 是给 `_init_skill_tools`
lazy MCP replay 用的 `frozenset[str]` 名字集，也是 active skills 的唯一权威源
（continuation turn 同样从这条事件读取路径得到，不会丢失）。

### 7.4 active skills 产出（取消独立字段）

旧的中间设计曾打算给 `AssemblyResult` 新增 `active_skills` 字段，并改 `_build_via_factory`
返回 `SessionSectionBuild(sections, active_skills)`，再在 root turn preparation 里把
`assembly.active_skills | resolution.active_skills` union 起来。

轻排序下**取消**这一改动：

- 给 `_init_skill_tools` 用的 `frozenset[str]` 名字集，已由 §7.3 的 `resolve_turn_intent`
  在 root pre-runtime 单一产出，并 backfill 进 `AgentRunRequest.active_skills`。
- prompt 里 loaded skills 段用的 `tuple[ActiveSkill, ...]` DTO，是 `SessionContextBuilder`
  在 `assemble_turn` 内部用 `skill_resolver(events)` 自算自用的，从不需要透出到
  `AssemblyResult` 或 `AgentRunRequest`。

因此 `AssemblyResult` 维持现状，不加字段；`SessionSectionBuild` 这个新 dataclass 与
`_build_via_factory` 的契约改动都不需要。active skills 只有一个权威源：`resolve_turn_intent`。

### 7.5 skill resolver 迁移

新增 `matmaster/context/skill_resolver.py` 或 `matmaster/skills/resolver.py`。

推荐路径：`matmaster/context/skill_resolver.py`，因为输出 DTO 是
`matmaster.context.ports.ActiveSkill`。

迁移内容：

- `SkillRegistryResolver`
- 依赖 `scan_skill_hits`
- 输出 `tuple[ActiveSkill, ...]`

`src/services/skill_resolver.py` 删除。若迁移需要分 PR，可以先保留薄导入壳，但最终不保留
deprecated 入口。

### 7.6 image input enrichment helper

替代旧版 `build_vision_image_parts`，新增：

```python
class ImageInputService:
    def resolve_image_detail(
        self,
        *,
        llm_config: LLMConfig,
        images: tuple[str, ...],
        llm_override: str | None,
        model_override: str | None,
        default_profile_key: str | None,
    ) -> Literal["low", "high", "auto"] | None:
        ...

    def enrich_turn_input_images(
        self,
        *,
        turn_input: TurnInput | None,
        user_prompt: str,
        top_level_images: tuple[str, ...],
        image_detail: Literal["low", "high", "auto"] | None,
    ) -> TurnInput:
        ...
```

`resolve_image_detail` 负责：

- 无图片时返回 `None`
- 有图片时调用 `ensure_vision_supported`
- 返回 profile 的 `vision_detail`

`enrich_turn_input_images` 负责：

- 统一处理 `images` 参数与 `turn_input.images`
- 保留当前已有的冲突 warning 语义
- 最终产出一个完整 `TurnInput`

## 8. 目标数据流

### 8.1 service 层

```
AgentRunService.run_agent
  ├─ init_playground_sync()
  ├─ environment = playground.prepare(RunMetadata(...), session_id=...)
  ├─ events_table = get_chat_events_table()
  ├─ fanout = build_run_event_fanout(...)
  ├─ exp_config = load_exp_config(...)
  ├─ run_bohrium_stage(...) -> environment + user_instructions
  ├─ llm_bundle = build_provider_bundle(...)
  ├─ turn_input = image_input_service.enrich_turn_input_images(...)
  ├─ figure_coordinator = FigureCoordinator(...)
  ├─ bridge = build_interaction_bridge(...)
  ├─ checkpoint_sink_factory = build_checkpoint_sink_factory(...)
  ├─ wiring = build_history_wiring(...)
  ├─ request = AgentRunRequest(
  │     llm_provider=llm_bundle.provider,
  │     llm_config=llm_config,
  │     llm_model=llm_bundle.model,
  │     llm_model_profile=llm_bundle.model_profile,
  │     llm_model_route=llm_bundle.model_route,
  │     interaction_bridge=bridge,
  │     turn_input=turn_input,
  │     user_instructions=user_instructions,
  │     active_skills=frozenset(),  # Exp pre-runtime 解析后 backfill
  │     bohrium_rebuild_events=tuple(wiring.bohrium_rebuild_events),
  │     ports=AgentRunPorts(...),
  │   )
  ├─ ctx = AgentRunContext(environment=environment, request=request)
  ├─ async for event in exp.run_stream(ctx, history=wiring.history, ...):
  │    ├─ normalize source
  │    ├─ dispatch
  │    ├─ figure_coordinator.record_tool_result(...)
  │    └─ capture RunResultEvent
  ├─ finalize
  └─ cleanup
```

注意：

- service 不再渲染 `user_turn_context`。
- service 不再创建 `ContextAssembler`。
- service 不再直接构造 `SkillRegistryResolver`。
- service 不再持有 `_active_skills`。

### 8.2 `Exp.run_stream`

变化点：删掉 `_prepare_root_run_context` 那个在 pre-runtime 里构造 registry / resolver /
context_runtime 的大块；pre-runtime 只剩一次廉价的 intent 解析；渲染挪到 `build_runtime`
之后，复用 `runtime_scope` 已经造好的那一份 assembler。

```
Exp.run_stream(ctx, task=None, *, history=None, cancel_token=None, spawn_id=None)

  # ── root pre-runtime（廉价，必须早于 build_runtime）──
  if spawn_id is None:
      if ctx.request.turn_input is None:          # 确定性错误，立即 fail-fast
          raise RuntimeError("AgentRunRequest.turn_input is required for root run")
      events_port = ctx.request.ports.compaction.history or EmptySessionEventHistory()
      user_instructions = ctx.request.user_instructions or UserInstructions(
          text="",
          hash=hash_user_instructions(""),
          truncated=False,
      )
      resolution = await resolve_turn_intent(     # 自由函数，只读事件，无 assembler/registry 依赖
          events_port=events_port,
          instructions_hash=user_instructions.hash,
          session_id=ctx.environment.session_id,
          spawn_id=None,
      )
      ctx = ctx.model_copy(update={"request": ctx.request.model_copy(
          update={"active_skills": resolution.active_skills},   # 唯一一次 backfill，单一来源
      )})

  # ── runtime_scope：build_runtime 造出唯一一份 assembler ──
  async with self.runtime_scope(ctx, cancel_token=cancel_token, spawn_id=spawn_id) as runtime:
      # build_runtime 内部 _init_skill_tools 读 ctx.request.active_skills 做 lazy MCP replay
      # runtime.context_runtime.assembler 就是这一份 assembler（经 §7.2 暴露）

      # ── render（build_runtime 之后、kernel 之前，仅 root）──
      if spawn_id is None:
          turn = await self._render_and_persist_root_turn(
              ctx=ctx,
              intent=resolution.intent,
              assembler=runtime.context_runtime.assembler,
              user_instructions=user_instructions,
          )
          task = turn.rendered_content
      # child path：task 由 child_run_factory 入参带入，不渲染、不写 root user_turn_context

      async for event in runtime.kernel.run_stream(
          runtime.kernel_runtime, task, history=history, cancel_token=cancel_token,
      ):
          yield event
```

`task` 参数是否保留：

- V2 第一阶段保留 `task: str | None = None`。
- service root path 不传 `task`，由 `ctx.request.turn_input` 渲染得到。
- child path 继续传 `task`，不写 root user_turn_context。
- devshell / evaluation 可继续通过 `runtime_scope` 直接驱动 kernel，暂不强制迁移。

这是比旧版一次性删除 `task` 更低风险的迁移策略。

关键约束（与原版相同，但满足方式更轻）：

- D5 仍然成立：active_skills 早于 `build_runtime`。由 pre-runtime 的一次 `resolve_turn_intent`
  满足，**不需要**在 pre-runtime 构造 assembler 或 registry。
- `build_runtime` 不新增 `prebuilt_*` 入参。O1 到 O3 可暂时保持
  `(ctx, *, skills, skill_resolver, spawn_id)`；O4 在 `Exp` 内部创建 resolver 后，再移除
  service 传入的 `skill_resolver` 参数。
- 渲染所需的 assembler 不是穿进去的，而是 `runtime_scope` 跑完 `build_runtime` 后从
  `runtime.context_runtime` 取出来的同一份。
- `events_port` 与 `user_instructions` 都有 core 内 fallback。生产 service 仍应注入真实
  history port 与 user instructions，但 `Exp` 不依赖非空假设。
- active skill 事件同时影响 `_init_skill_tools` 的 lazy MCP replay 与 prompt 中的 loaded
  skills 段。前者依赖 `resolve_turn_intent` 产出的名字集，必须在 `build_runtime` 前
  backfill；后者由 assembler 内部 `skill_resolver(events)` 自算。渲染产出的 `task` 内容只在
  `kernel.run_stream` 时消费，放在 `build_runtime` 之后即可。

### 8.3 root turn 的两个时点

变化点：原 `_prepare_root_turn` 把 intent 解析 + 渲染 + 写入捆在 `build_runtime` 之前一次做完；
现在拆成 pre-runtime 的 intent 解析（§7.3 的自由函数，已在 §8.2 调用）和 post-build_runtime
的渲染 + 写入。active skills 在前一个时点单一产出，渲染不再回吐 active skills。

```python
@dataclass(frozen=True)
class RootTurnRender:
    rendered_content: str


async def _render_and_persist_root_turn(
    self,
    *,
    ctx: AgentRunContext,
    intent: ContextAssemblyIntent,
    assembler: ContextAssembler,          # = runtime.context_runtime.assembler，不再二次构造
    user_instructions: UserInstructions,
) -> RootTurnRender:
    assembly = await assembler.assemble_turn(
        intent=intent,
        request=TurnAssemblyRequest(
            session_id=ctx.environment.session_id,
            spawn_id=None,
            turn_input=ctx.request.turn_input,
            user_instructions=user_instructions,
        ),
    )
    message = assembly.user_turn_context.to_message(ContextView.RUNTIME)
    await self._write_user_turn_context_if_configured(
        ctx=ctx, intent=intent, message=message, user_instructions=user_instructions,
    )
    return RootTurnRender(rendered_content=message.content)
```

要点：

- 不再返回 `active_skills`——它已在 §8.2 的 pre-runtime 由 `resolve_turn_intent` 产出并
  backfill 完毕（唯一权威源，§7.4）。
- prompt 里 loaded skills 段用的 `ActiveSkill` DTO，是 `SessionContextBuilder` 在
  `assemble_turn` 内部用 `skill_resolver(events)` 自算自用的，不经过这里。
- `turn_input` 的必要性校验已前移到 §8.2 的 pre-runtime（缺则在 `build_runtime` 之前
  fail-fast），这里不再重复抛。

`_write_user_turn_context_if_configured` 调用 `ctx.request.ports.user_turn_context_writer`。
如果 writer 为 `None`，仅跳过持久化。生产 service 必须注入 writer；devshell / tests 可为空。

## 9. FigureCoordinator

新增 `src/services/figure_coordinator.py`。

职责：

- 持有 `ResponseFiguresAccumulator`
- 持有 `asyncio.Lock`
- 封装 final flush
- 封装 root tool result 记录
- 封装 child event sink
- 持有 `FigureUploadConfig`

接口：

```python
class FigureCoordinator:
    def __init__(
        self,
        *,
        fanout: RunEventFanout,
        session_id: str,
        task_id: str,
    ) -> None: ...

    @property
    def upload_config(self) -> FigureUploadConfig: ...

    async def child_event_sink(self, event: BusEvent) -> None: ...

    async def flush_if_dirty(self, reason: str) -> None: ...

    async def record_tool_result(
        self,
        event: ToolResultEvent,
        *,
        include_spawned: bool,
        reason: str,
    ) -> None: ...
```

约束：

- dispatch 前继续调用 `fanout.flush_persistence_barrier()`。
- snapshot dispatch 成功后才调用 `mark_snapshot_emitted()`。
- dispatch 失败只 warning，不中止 agent run。
- child event sink 失败只 warning，不传播到 child runtime。

## 10. AskQuestionBridge 小工厂

新增 `src/services/interaction_bridge_factory.py` 或放在 `agent_run_service.py` 私有 helper。

推荐先用私有 helper，避免为了 4 行逻辑新建文件。

```python
def _build_interaction_bridge(
    *,
    session_id: str,
    fanout: RunEventFanout,
) -> AskQuestionBridge:
    async def event_sink(event: BusEvent) -> None:
        await fanout.dispatch(event)

    return AskQuestionBridge(
        session_id=session_id,
        event_sink=event_sink,
        reply_queue=RedisReplyQueue(session_id),
        timeout_seconds=1800,
    )
```

这一项不是核心架构要求，只是让 `run_agent` 主体更容易阅读。

## 11. 迁移阶段

### O1：低风险 service 清理

目标：

- 引入 `FigureCoordinator`
- 引入 image input enrichment helper
- 提取 `_build_interaction_bridge`
- 提取 `_build_fanout`
- 提取 `_finalize_run`
- 提取 `_cleanup_run`

不动：

- `Exp.run_stream` 签名
- `ContextAssembler` 下沉
- active skills 路径
- skill resolver 路径

验收：

- response figures 相关测试通过
- image input 相关测试通过
- `AgentRunService.run_agent` 里的 figure accumulator / lock / child sink 闭包消失
- service 中不再手写 image detail merge 细节

### O2：删除 active skills 热缓存

目标：

- service 删除 `_active_skills` 进程内热缓存
- service 删除 `_remember_skill_hit`
- `_resolve_active_skill_names` 改为每轮直接从 session events `scan_skill_hits` 产出，
  不再读写进程内 cache

不动：

- `AssemblyResult` 契约（轻排序不给它加 `active_skills` 字段，见 §7.4）
- `Exp.run_stream` 签名
- skill resolver 路径

临时状态：

- 这段 active skills 扫描此刻仍在 service 层；O4 会把它下沉为 `Exp` root pre-runtime 的
  `resolve_turn_intent`（§7.3），service 届时不再扫描。

验收：

- lazy MCP replay 测试仍通过
- active skill prompt rendering 仍包含过去 turn 激活的 skill
- 多实例语义不依赖本地缓存

### O3：skill resolver 提升到 `matmaster` 包内

目标：

- 新增 `matmaster/context/skill_resolver.py`
- 移动 `SkillRegistryResolver`
- `src/services/skill_resolver.py` 删除或仅在过渡 PR 中保留薄壳
- 更新 service 与 runtime 侧 import 路径，但暂不改变运行时调用契约
- `AgentRunService` 在 O3 后仍可构造 resolver 并传给 `Exp.run_stream` /
  `runtime_scope` / `build_runtime`

不动：

- 不删除 `run_stream(..., skill_resolver=...)` 这一条调用链
- 不要求 `Exp.build_runtime` 在本阶段内部自造 resolver
- ~~抽共享 registry 构造 helper~~——删除。轻排序下 pre-runtime 不构造 registry，
  真正收口到单构造放在 O4 处理

验收：

- `matmaster/core/exp.py` 不 import `src.services.*`
- `SkillRegistryResolver` 的权威实现不在 `src/services` 下
- service 若仍引用 `SkillRegistryResolver`，也必须从 `matmaster/context/skill_resolver.py`
  导入，而不是从 `src.services` 导入
- skill hit replay 与 lazy MCP 注入仍通过

### O4：root turn rendering 下沉到 `Exp`

目标：

- 新增 `AgentRunPorts.user_turn_context_writer`
- `AgentRuntime` 携带 `context_runtime`（§7.2：`build_runtime` return 时把已构造的
  `ContextAssemblyRuntime` 多挂一个字段，小改）
- 迁移 `src/services/context_turn_intent.py` → `matmaster/context/turn_intent.py` 的自由函数
  `resolve_turn_intent`（§7.3）
- `Exp.run_stream` 对 root run：
  - `build_runtime` 之前：校验 `turn_input`、一次 `resolve_turn_intent`、backfill `active_skills`
  - `build_runtime` 之后、kernel 之前：`_render_and_persist_root_turn`
- `Exp` 在 `_init_skill_tools` / `build_runtime` 路径内部创建 O3 已迁移的
  `SkillRegistryResolver`
- service 删除 resolver 构造与传参，`Exp.run_stream` / `runtime_scope` / `build_runtime`
  的 public path 不再要求 `skill_resolver`
- ~~`build_runtime` 接收 prebuilt `skill_registry`、`skill_resolver` 与 `context_runtime`~~
  ——删除；允许移除旧的 `skill_resolver` 参数，但不允许新增 prebuilt 参数
- service 删除 Stage 5b：
  - `build_context_assembler`
  - `resolve_turn_context_intent`
  - `TurnAssemblyRequest`
  - `ContextView`
  - `write_user_turn_context_event`
  - payload 构造
- service root path 调用 `exp.run_stream(ctx, history=history, cancel_token=...)`

保留：

- child run factory 可以继续传 `task`
- devshell 可以继续通过 `runtime_scope` 直接驱动 kernel

验收：

- root turn 会写入 `user_turn_context`
- writer 失败会中止当前 turn
- spawn run 不写 root `user_turn_context`
- `AgentKernelSpec` 和 `AgentKernelResources` 不暴露 context assembly internals
- `build_runtime` 公共签名未出现 `prebuilt_*` 入参（反向断言，防止 prebuilt 穿线回流）
- `rg -n "SkillRegistryResolver" src/services/agent_run_service.py` 无结果
- service boundary 测试通过

### O5：删除 service 层旧 context assembly adapter

目标：

- 删除 `src/services/context_assembly_factory.py`
- 删除 `src/services/context_turn_intent.py`
- 删除 `src/services/context_assembly_ports.py` 中只为 service assembly 存在的部分

保留检查：

- 如果 `AppUserInstructionsPort` 仍有 caller，单独迁移或保留到更合适模块。
- 删除前用 `rg` 确认无生产 caller。

## 12. 测试策略

### 12.1 Boundary tests

新增 `tests/matmaster/services/test_agent_run_service_orchestration_boundary.py`。
这些是完成 O4/O5 后的最终边界断言；O3 期间 service 仍可临时引用已迁移到
`matmaster/context/skill_resolver.py` 的 resolver。

断言：

- `agent_run_service.py` 不 import `ContextAssembler`
- `agent_run_service.py` 不 import `build_context_assembler`
- `agent_run_service.py` 不 import `resolve_turn_context_intent`
- `agent_run_service.py` 不 import `write_user_turn_context_event`
- `agent_run_service.py` 不包含 `_active_skills`
- `agent_run_service.py` 不包含 `_resolve_active_skill_names`
- `agent_run_service.py` 不包含 `SkillRegistryResolver`

新增 `tests/matmaster/core/test_exp_turn_preparation.py`。

断言：

- root run 缺 `turn_input` 会失败（在 `build_runtime` 之前 fail-fast）
- root run 调用 writer
- writer 失败向外抛出
- spawn run 不调用 writer
- writer 为空时可跳过持久化，供 devshell / low-level tests 使用
- `build_runtime` 公共签名不含 `prebuilt_*` 入参
- O4 后 `Exp.run_stream` / `runtime_scope` / `build_runtime` 不再要求 service 传
  `skill_resolver`
- `ctx.request.ports.compaction.history is None` 时使用 `EmptySessionEventHistory`
- `ctx.request.user_instructions is None` 时使用空 `UserInstructions`

新增或更新 `tests/matmaster/types/test_runtime_ports.py`。

断言：

- `AgentRunPorts.user_turn_context_writer` 默认 `None`
- `AgentRunPorts` 不含 `dict[str, Any]` 兜底字段
- writer request 是 typed dataclass

### 12.2 Unit tests

`tests/matmaster/services/test_figure_coordinator.py`：

- dirty snapshot dispatch 成功后 mark emitted
- dispatch 失败不 mark emitted
- child `ToolResultEvent` 用 `include_spawned=True`
- root `ToolResultEvent` 用 `include_spawned=False`
- concurrent record 调用由 lock 串行化

`tests/services/test_image_input_service.py`：

- 无图片不调用 vision capability check
- 有图片且模型不支持 vision 时抛 `ImageInputError`
- 有图片时返回 profile `vision_detail`
- top-level images 与 `TurnInput.images` 冲突时保留当前优先级

`tests/matmaster/context/test_turn_intent.py`：

- 自由函数 `resolve_turn_intent` 与旧 `resolve_turn_context_intent` 等价
- anchor turn 返回 active skill names
- continuation turn 不读取全量 session sections 时，active skills 仍由事件扫描得到
- 不依赖 `ContextAssembler` 实例即可调用（只吃 `events_port`）
- intent 解析与 active skill 扫描使用不同 event query；active skill 不会被 intent 的小窗口截断

### 12.3 Integration tests

沿用并更新：

- `tests/matmaster/services/test_agent_run_stream.py`
- `tests/matmaster/services/test_agent_run_stream_images.py`
- `tests/matmaster/services/test_agent_run_stream_response_figures.py`
- `tests/matmaster/services/test_lazy_mcp_replay.py`
- `tests/matmaster/core/test_exp_runtime_v2.py`
- `tests/matmaster/test_runtime_spec.py`

关键回归：

- current-turn images 仍进入 `AgentKernelSpec.turn_input`
- response_figures 对 root 与 child tool result 仍正确累积
- compaction history port 仍由 `build_history_wiring` 提供
- Bohrium rebuild events 仍进入 `AgentRunRequest.bohrium_rebuild_events`
- AskQuestion root run bridge 仍可用，spawn run 不接 root bridge

### 12.4 验证命令

使用项目 uv 环境：

```bash
uv run pytest \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_agent_run_stream_images.py \
  tests/matmaster/services/test_agent_run_stream_response_figures.py \
  tests/matmaster/services/test_lazy_mcp_replay.py \
  tests/matmaster/core/test_exp_runtime_v2.py \
  tests/matmaster/test_runtime_spec.py \
  tests/services/test_image_input_service.py
```

完成所有阶段后再跑：

```bash
uv run pytest tests/matmaster/services tests/matmaster/core tests/matmaster/types
uv run pre-commit run --all-files
```

## 13. 风险与处理

### R1：`AgentRuntime.context_runtime` 被误用为 kernel-facing 字段

处理：

- 边界测试继续断言 `AgentKernelSpec` 与 `AgentKernelResources` 不含 context assembly internals。
- 文档明确 `AgentRuntime` 是 `Exp` lifecycle bundle，不传给 kernel。

### R2：root turn rendering 下沉后 writer 失败语义改变

处理：

- writer 失败抛异常。
- `AgentRunService` 现有异常分支统一发 `ErrorEvent + StreamClosedEvent`。
- 增加测试覆盖 writer failure。

### R3：child run 被误写 user_turn_context

处理：

- root pre-runtime 的 intent 解析/backfill 与 `_render_and_persist_root_turn` 都只在
  `spawn_id is None` 时执行。
- child factory 传入 task 后直接驱动 kernel。
- 测试断言 spawn run writer 未调用。

### R4：active skills 对 continuation turn 的语义不清

处理：

- root anchor turn 读取 session events 并返回 active skills。
- continuation turn 若不读取 session sections，仍由 `resolve_turn_intent` 的独立
  `skill_hit` 查询得到 active skills。
- 本 spec 要求：O4 中让 `resolve_turn_intent` 返回 `TurnIntentResolution`，包含 intent
  与 `active_skills`。intent 读取小窗口，active skills 读取单独窗口，避免 continuation
  turn 因最近事件窗口太小而丢失已激活技能。

推荐模型：

```python
@dataclass(frozen=True)
class TurnIntentResolution:
    intent: ContextAssemblyIntent
    active_skills: frozenset[str] = frozenset()
```

### R5：（已消除）registry 双构造

O4 完成后，`SkillRegistry` 只在 `build_runtime` 路径内构造一次，root pre-runtime 阶段
只调 `resolve_turn_intent`（纯事件扫描），不构造 registry。原先两份 registry 的风险不复存在。
把 `build_skill_registry` 抽到 `matmaster/skills/factory.py` 让 service 与 Exp 复用，
降级为与本 spec 无依赖的可选清理。

### R6：渲染后置导致 writer 失败时浪费一次 `build_runtime`

轻排序把 turn 渲染挪到 `build_runtime` 之后，因此 writer 写失败发生在 runtime 构造之后。

处理：

- 最常见的硬错误是缺 `turn_input`，这是确定性的，已在 `build_runtime` 之前校验并 fail-fast，
  不浪费构造。
- 只有 writer 的 DB 写失败（极低频）会发生在 `build_runtime` 之后；此时 `runtime_scope`
  的 `finally` 已保证 runtime 清理（MCP 连接、cleanup callbacks），代价仅是一次已完成的
  构造被丢弃。
- 取舍：用罕见失败路径的一次构造浪费，换 happy path 永久免除 `_init_skill_tools` 的 prebuilt
  参数化耦合。

### R7：devshell / evaluation caller 被破坏

处理：

- V2 不一次性删除 `Exp.run_stream(task=...)`。
- devshell 继续用 `runtime_scope` 直接驱动 kernel。
- 等生产 path 稳定后，再单独设计 devshell/evaluation turn_input 化。

## 14. 完成定义

本 spec 完成后，当前 checkout 应满足：

- `AgentRunService` 不再直接构造 context assembler。
- `AgentRunService` 不再直接解析 turn intent。
- `AgentRunService` 不再直接写 user_turn_context event。
- `AgentRunService` 不再持有 `_active_skills` 进程内缓存。
- `AgentRunService` 不再构造 `SkillRegistryResolver`。
- response figures 协调逻辑在 `FigureCoordinator`。
- root turn rendering 在 `Exp.run_stream` 内完成。
- `AgentKernelSpec` 与 `AgentKernelResources` 仍不暴露 context assembly internals。
- service -> runtime 能力端口通过 `AgentRunPorts` 表达，不通过 metadata 或 dict-bag 夹带。
- API / Worker 分离语义保持不变。

## 15. 推荐执行顺序

推荐按 O1 到 O5 分 PR 执行：

1. O1 先清理 figure 与 image helper，降低 `run_agent` 局部复杂度。
2. O2 删除 `_active_skills` 热缓存，先消除最明显的多实例状态风险。
3. O3 移动 skill resolver 边界，解除 `Exp` 下沉的 import 障碍。
4. O4 下沉 root turn rendering，这是最大行为改动。
5. O5 删除旧 service assembly adapter，做最终边界收口。

不要把 O1 到 O5 合成一个大 MR。O4 风险最高，应在 O1 到 O3 稳定后独立 review。
