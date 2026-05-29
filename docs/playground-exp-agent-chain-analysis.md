# Playground → Exp → Agent 链路分析与职责漂移诊断

- **分析日期:** 2026-05-29
- **分析分支:** `refactor/context`（HEAD `c8c562bf`）
- **范围:** `matmaster/core/`（playground / exp / agent）、`matmaster/context/`、`matmaster/types/`、`src/services/`、`matmaster/devshell/`
- **方法:** 直读 7 个核心文件 + 5 路并行子系统映射 + 全量 git 历史回溯 + 8 条假设的对抗性验证（7 confirmed / 1 partial）
- **关联文档:** [.planning/codebase/ARCHITECTURE.md](../.planning/codebase/ARCHITECTURE.md)（描述**理想**三层切分）、[.planning/context-refactor/DESIGN.md](../.planning/context-refactor/DESIGN.md)、[docs/superpowers/plans/2026-05-18-run-meta-refactor.md](superpowers/plans/2026-05-18-run-meta-refactor.md)

---

## 0. 结论摘要

原始三层切分在**诞生当天是干净的**，随后向**两个方向同时侵蚀**：

- **方向 A — Exp 膨胀：** 从纯组装（config+ctx→spec）→ 吞下 subagent 编排（2026-03-25）→ 吞下运行时构建 + kernel 驱动（2026-04-02/03）。`exp.py` 从 257 行长到 926 行。
- **方向 B — PlaygroundContext 退化为 god-object：** 从环境快照 → 携带 `llm_provider`（诞生当天）/ `interaction_bridge` / `runtime_ports` / 一整袋 metadata。它已经成为 `prepare() → run_stream()` 之间承载全部 run-state 的总线。

**最底层守住了：** [AgentKernel](../matmaster/core/agent.py:105) 仍然纯净，只把 `AgentRuntimeSpec` 当作唯一运行时配置对象，对 Playground / Exp / Session / config 零认知。`task` / `history` / `cancel_token` 是执行输入，不改变配置边界；blur 全部集中在 Playground / Exp / Service 这个上游三角。

**对 Exp 现状的判断：** 核心（配置驱动的运行时装配器）合理且应守住；不合理的是它顺带承包了**运行作用域**与 **subagent 编排**——这两件事恰好会因完全不同的原因而变化（违反单一职责）。

---

## 1. 链路全景（已逐边验证）

一次 web 请求的真实数据流，入口是 [AgentRunService.run_agent](../src/services/agent_run_service.py:218)：

```
AgentRunService.run_agent(session_id, user_prompt, ...)
│
├─ Stage1  PlaygroundManager.get_or_create(session_id)         # agent_run_service.py:271
│          Playground.prepare(RunMetadata)                     # :273 → 返回 frozen PlaygroundContext
│                                                              #   prepare 只填物理字段
│
├─ Stage3  run_bohrium_stage(playground, pg_ctx, ...)          # :322 远程容器 + SSH（见 §6）
│          → pg.session 被原地换成 ssh_session                 # agent_run_bohrium.py:708
│          → pg_ctx.with_bohrium() / with_execution() 重写快照 # agent_run_bohrium_stage.py:110/114
│
├─ Stage4  pg_ctx.model_copy(update={llm_provider, llm_config})# :373 ← 注入模型能力
│          pg_ctx.with_updates(runtime_ports={figure_upload})  # :484
│          Exp(exp_config)                                     # :385
│ Stage4b  pg_ctx.model_copy(update={interaction_bridge})      # :502
│ Stage5   pg_ctx.with_updates(runtime_ports={                 # :513
│             child_event_forward_sink, compaction})
│          pg_ctx.with_updates(metadata={bohrium_rebuild_events,# :520/529/582/640
│             user_instructions, turn_input, active_skills})
│
└─ Stage6  exp.run_stream(pg_ctx, user_prompt, history, ...)   # :647
              │
              └─ Exp.run_stream                                # exp.py:548 ← 这里开始"跑模型"
                    ├─ Exp.build_runtime(ctx)                  # exp.py:356 造全部运行时资源
                    │     ToolRegistry / builtin+skill+MCP+Agent 工具
                    │     SystemPromptBuilder / RuntimeTopology / FullToolRunner
                    │     ToolScheduler / ToolRunnerState / JobRegistry
                    │     build_runtime_context_assembly → compactor + context_assembler
                    │     AgentKernel() → AgentRuntime{kernel, spec, cleanup}
                    ├─ 注入 cancel_token 到 session / tool_catalog  # :574 / :579
                    └─ runtime.kernel.run_stream(spec, task)   # exp.py:581 → agent.py:108
                          └─ AgentKernel：纯 LLM→hook→tool→accumulate 循环
```

**关键计量：** service 层在 [prepare()](../src/services/agent_run_service.py:273) 之后，对号称 frozen 的 `PlaygroundContext` 做了精确 **9 次** `model_copy` / `with_updates` 注入，才喂给 Exp；若把 Bohrium stage helper 内部的 `with_bohrium` / `with_execution` 一并算作 service 层注入，则共 **11 次**。这个数字本身就是职责边界破裂的体温计。

数据契约定义点：
- [PlaygroundContext](../matmaster/core/playground.py:55) — frozen，Playground→Exp 的传递对象
- [AgentRuntimeSpec](../matmaster/types/runtime.py:48) — frozen，Exp→Kernel 的传递对象
- [AgentRuntime](../matmaster/types/runtime.py:188) — `build_runtime()` 产出的 bundle（kernel + spec + cleanup）

---

## 2. 演化时间线 —— 为什么会越来越混淆

git 历史（全量 4603 commit，非浅克隆，可信）。三层**同一天诞生且边界干净**，然后双向侵蚀。

| 时间 | commit | 发生了什么 | 方向 |
|------|--------|-----------|------|
| 03-22 00:43 | `1ff1027d` | AgentKernel 诞生，唯一职责 = 跑循环 | 基线 |
| 03-22 10:54 | `f99d69cf` | **ContextBuilder 作为独立 core 模块诞生** —— 组装上下文本来就不在 Exp 里 | 基线 |
| 03-22 11:02 | `4141f15b` | Exp 诞生，干净的 config+ctx→spec | 基线 |
| 03-22 14:10 | `4b93fa7f` | Playground 诞生，只管 workspace/session/cache/log | 基线 |
| **03-22 23:41** | `072e868d` | **诞生当天**就给 PlaygroundContext 加了 `llm_provider` 字段 | ← 第一处泄漏 |
| 03-25 | `b04e55e3` | Exp 吞下 subagent spawn，`394→455` 行（+61） | → Exp 膨胀 #1 |
| 04-02 | `9fea6339` | Exp 扩展既有 `build_runtime` 并新增 `run_stream`，`516→622` 行（+106） | → Exp 膨胀 #2 |
| 04-03 | `5b0aec81` | 删掉 `Exp.run()`，`run_stream` 成为唯一入口 | → Exp 膨胀 |
| 04-17 | `39c7a1e1` | `interaction_bridge` 塞进 context | ← 泄漏 |
| 05-11 | `6c6c4bbc` | `runtime_ports` 能力包塞进 context，`exp.py→906` | ← 泄漏 |
| 05-16 | `2109de1d` 等一串 | **context/ 包整体诞生**，把 prompt/compaction/assembly 从 Exp 抠回去 | ↺ 部分纠偏 |
| **05-17** | `f24ecde7` | **把 PlaygroundContext 整体搬进 `core/playground.py`**，连同 `llm_provider/runtime_ports/interaction_bridge/llm_config` | ← 泄漏加深 |

**两点值得强调：**

1. 组装上下文这件事**本来就是独立 core 模块**（`f99d69cf` 的 ContextBuilder），不是 Exp 的。它被吸进 Exp 后，团队在 05-16 又用 context/ 包把它抠回去。
2. 当前分支就叫 `refactor/context`，05-16 的 context/ 包正是团队**已经意识到 blur 并开始反向纠偏**的产物。本文档是这场重构的现状底图。

---

## 3. 方向 B 细节：PlaygroundContext 已是 god-object

[Playground](../matmaster/core/playground.py:164) 这个**类**是守纪律的——[prepare()](../matmaster/core/playground.py:209) 只填物理字段。**泄漏不在类里，在契约的形状里**：[PlaygroundContext](../matmaster/core/playground.py:55) 声明了一堆 agent 运行期字段，并暴露 [with_updates](../matmaster/core/playground.py:130) / [with_bohrium](../matmaster/core/playground.py:121) / [with_execution](../matmaster/core/playground.py:106) / `model_copy` 作为注入口子。

### 逐字段归属审计

| 字段 | 归属 | 谁填的 | 谁消费 |
|------|------|--------|--------|
| `workdir` / `session_type` / `execution_workdir` / `session` / `archival` | ✅ 物理环境 | `prepare()` | Exp 工具目录 / 上传 |
| `cache_area` | ⚠️ 物理且 core 内创建（`prepare()` 中 `:256-257` 建目录 / `:264` 写字段）但无下游消费者读取 | `prepare()` | — |
| `env_vars` | ⚠️ 物理但 [_collect_env_vars](../matmaster/core/playground.py:480) **恒返回 `{}`** + 无人消费 | `prepare()` | — |
| `llm_provider` | ❌ **模型能力**（docstring 明列为非职责） | service [:373](../src/services/agent_run_service.py:373) | [Exp.assemble:312](../matmaster/core/exp.py:312) |
| `llm_config` | ❌ 模型路由配置 | service [:381](../src/services/agent_run_service.py:381) | — |
| `interaction_bridge` | ❌ 工具运行期能力（AskQuestion 人在环回路） | service [:502](../src/services/agent_run_service.py:502) | [Exp._init_builtin_tools:674](../matmaster/core/exp.py:674) |
| `runtime_ports`（整包） | ❌ **可调用能力包**（其 docstring 自称非 metadata 容器） | service [:484](../src/services/agent_run_service.py:484) / [:513](../src/services/agent_run_service.py:513) | exp.py `:485/:500/:504`、`runtime_context_assembly:71` |
| `metadata.turn_input` | ❌ 当轮用户输入 | service [:278](../src/services/agent_run_service.py:278) / [:582](../src/services/agent_run_service.py:582) | [Exp.build_runtime:532](../matmaster/core/exp.py:532) |
| `metadata.user_instructions` | ❌ 上下文组装产物 | service [:529](../src/services/agent_run_service.py:529) | [runtime_context_assembly:75](../matmaster/core/runtime_context_assembly.py:75) |
| `metadata.active_skills` | ❌ skill 注册表状态（docstring 明列为非职责） | service [:640](../src/services/agent_run_service.py:640) | [exp.py:898](../matmaster/core/exp.py:898) |
| `metadata.bohrium_rebuild_events` | ❌ 工具状态重建事件 | service [:520](../src/services/agent_run_service.py:520) | [exp.py:482](../matmaster/core/exp.py:482) |
| `config_dir` | 🪦 **死字段**：从不被设非空、从不被读 | — | — |
| `session_id` | 🤷 身份，与 `RunIdentity.session_id` 重复（两处），并经 `AgentRuntimeSpec.run_identity` 间接第三次出现 | `prepare()` | exp/context 层 |

### 最大泄漏向量：metadata 袋子

[RunMetadata](../matmaster/types/run_metadata.py:25) 把真正的 run 身份（`run_dir`/`task_id`/`source`）和 4 个纯 agent 运行期字段混在一起，`with_updates(metadata=...)` 在 service 里被调了 **5 次**。

文档与契约自相矛盾的铁证：[playground.py:10-13](../matmaster/core/playground.py:10) 的 docstring 白纸黑字写非职责包含 "Skill registry / LLM provider"，而这个对象正好携带 `active_skills` 和 `llm_provider`；class docstring [:61-63](../matmaster/core/playground.py:61) 已把 `llm_provider` 写进环境 context 说明，字段旁注释 [:90-92](../matmaster/core/playground.py:90) 又专门解释为何它只能标成 `Any`。

---

## 4. 方向 A 细节：Exp 三阶段里"组装"已是最小的那个

[Exp](../matmaster/core/exp.py:155) 自称 config-driven assembly layer，但三阶段分量完全倒挂：

- **[assemble()](../matmaster/core/exp.py:308)** —— 真正纯粹的 config+ctx→spec，**全文 8 行**，三阶段里最小，且只被 `build_runtime` 调用。
- **[build_runtime()](../matmaster/core/exp.py:356)** —— 构造 `ToolRegistry`、注册 builtin/skill/MCP/Agent 工具、`SystemPromptBuilder`、`RuntimeTopology`、`FullToolRunner`、`ToolScheduler`、`ToolRunnerState`、`JobRegistry`、compactor、context_assembler，最后 [AgentKernel():538](../matmaster/core/exp.py:538)。整个运行时资源工厂（188 行）。
- **[run_stream()](../matmaster/core/exp.py:548)** —— 直接驱动 `runtime.kernel.run_stream` 并逐事件转发（[:581](../matmaster/core/exp.py:581)），往 session/catalog 注 cancel_token，用 try/finally 保证 cleanup。这是执行驱动。

### subagent 编排（最直接的"exp 负责模型运行"证据）

[_make_spawn_fn](../matmaster/core/exp.py:216)：

```
父 Exp.build_runtime 检测到 builtin 含 "Agent" 且 allow_spawn=True
  → 造 spawn_fn = Exp._make_spawn_fn(...)               # exp.py:436
  → AgentTool(spawn_fn=...)  ← AgentTool 只是空壳，execute 只转发 spawn_fn
模型调用 Agent 工具
  → spawn_fn: load_exp_config → 子 Exp(child_config, allow_spawn=False)  # :239 一层递归封顶
  → 发 HookEvent.SUBAGENT_START                          # :267
  → drain(子Exp.run_stream(...), on_event=_forward_child_event)  # :277 跑完整子 kernel 循环
  → _forward_child_event: model_copy 改写每个事件 source/spawn_id     # :245
       推给 ctx.runtime_ports.child_event_forward_sink   ← 子流多路复用进父流
  → finally 发 SUBAGENT_STOP                             # :289
```

Exp 同时承担了：子 agent 实例化、生命周期 hook、事件流多路复用与重打标、递归深度策略（`allow_spawn`）、spawn_id 铸造。[AgentTool](../matmaster/tools/builtin/agent_tool.py:200) 退化成只转发闭包的空壳——orchestration 整个被上提进了 Exp。

---

## 5. context 组装职责被"三裂"（对抗验证修正项）

初步判断"Exp 的组装职责已整体迁出 context/ 包"被对抗验证降级为 **partial**。准确画面是组装上下文这个本该是 Exp 核心身份的职责被**三裂**：

1. **轮次/历史/压缩组装** —— 确实迁出。[ContextAssembler](../matmaster/context/assembly.py:125)、section 渲染器（`context/sources/*`）、composition 管线在 context/ 包里。Exp 只通过 [build_runtime_context_assembly](../matmaster/core/runtime_context_assembly.py:59)（[exp.py:469-475](../matmaster/core/exp.py:469)）接线进 spec。
2. **system prompt 组装** —— **没迁走**，仍由 [Exp.build_runtime 内联调 SystemPromptBuilder](../matmaster/core/exp.py:455)，因为它依赖 Exp 自己刚建好的 `ToolRegistry`。
3. **当轮用户消息组装** —— 迁得比 context/ 包**还远**，落到了 **service 层**：[agent_run_service.py:584](../src/services/agent_run_service.py:584) 自己 new 了一个 `ContextAssembler` 调 `assemble_turn`。Exp 接进 spec 的那个 `context_assembler`，正常轮次里**只被 ContextCompactor 消费**，不参与首轮用户消息。

佐证：`runtime_context_assembly`（`session_events` 从 `ctx.runtime_ports.compaction.history` 取得，缺省才回退到 empty port；`session_jobs` 当前恒空）和 [context_assembly_factory](../src/services/context_assembly_factory.py:17)（喂 DB-backed `session_events` / `session_jobs` ports）是**两套近乎重复**的 assembler 构造器——职责没人独占、被切碎散落的典型征兆。

---

## 6. Bohrium：物理职责却由 service 层越界操刀

远程容器 + SSH 本是平台/物理职责，但它住在 `src/services/` 且完全由 service 驱动，还**绕过 Playground 自己的 API** 原地改 Playground：

- [prepare()](../src/services/agent_run_service.py:273)（Stage1）先在本地建好 session；Stage3 才在 worker 线程里分配 Bohrium 节点，然后 [pg.session = ssh_session; pg._owns_session = False](../src/services/agent_run_bohrium.py:708) **直接赋值**，并把原 session 存进全局 `SESSIONS` 字典留待 cleanup 还原。
- Playground 其实有干净的 [attach_ssh_session](../matmaster/core/playground.py:311) / [detach_session](../matmaster/core/playground.py:354)，但 Bohrium 路径**不用它**——出现两套并行的 session 切换机制，干净那套形同虚设。
- 同一物理事实（当前哪个 session 活跃）被**表达两遍**：原地改的 `pg.session` + 快照重写的 `pg_ctx.with_execution()`，靠手工保持一致。
- Playground 自己供认了这个倒置——[playground.py:198-199](../matmaster/core/playground.py:198) 注释 "Kept directly writable (per Pitfall 3: agent_run_bohrium.py does `pg.session = ssh_session`)"。

---

## 7. 唯一守住的边界：AgentKernel 仍然纯净

[AgentKernel](../matmaster/core/agent.py:105) 经验证是真正纯的：

- [run_stream](../matmaster/core/agent.py:108) 接收 `spec` / `task` / `history` / `cancel_token`；其中 `AgentRuntimeSpec` 是唯一运行时配置对象，该类型仅在 `TYPE_CHECKING` 下导入（[agent.py:49-50](../matmaster/core/agent.py:49)）。
- 整个 import 块**没有** Playground / PlaygroundContext / Exp / ExpConfig / sessions 的任何引用。
- kernel 里的 `session_id` 读的是 `spec.run_identity.session_id`（纯字符串身份），**从不碰活的 Session 对象**。
- 所有副作用都通过 spec 字段触达（`spec.llm_provider` / `tool_catalog` / `compactor` / `hook_executor` / `runtime_ports`）。

**结论：腐蚀没有到达最底层。重构动上游不会波及执行核心。**

---

## 8. devshell 第二条接线：印证"本质链路 vs service 堆积"

[matmaster/devshell/runner.py](../matmaster/devshell/runner.py) 是不经 FastAPI 的第二条驱动路径，它的取舍恰好划出哪些必须、哪些是 service 堆出来的：

- devshell **完全不用 Playground 类**，直接手搓 PlaygroundContext（[runner.py:58-67](../matmaster/devshell/runner.py:58)），还把 `llm_provider/llm_config` 直接塞进构造函数——再次证明泄漏在**类型契约本身**。
- devshell **绕过 [Exp.run_stream](../matmaster/core/exp.py:548)**，自己调 `build_runtime` + `kernel.run_stream`，手工复制了 cancel 注入和 cleanup（[runner.py:153-173](../matmaster/devshell/runner.py:153)）。
- **最小本质链路**（两条路径都做）：build_provider → 携带 workdir/session/llm_provider 的 ctx → 注入 `child_event_forward_sink`（**唯一必要的 post-prepare port 注入**）→ `build_runtime` → `kernel.run_stream` → drain。
- **纯属 service 堆积**（devshell 一概不做）：`interaction_bridge`、`figure_upload`、`compaction`、`user_instructions`/`active_skills`/`turn_input` 上下文组装、Bohrium SSH、fanout/SSE/persistence/Redis/quota。

> **决定性症状：** 一个职责如果只有原配调用者能用、第二个调用者必须复制实现（devshell 抄 `run_stream` 的身子），那这个职责的边界就划在了错误位置。

---

## 9. Exp 现有职责的合理性评估

用单一职责的"变更理由"检验法数 Exp 当前应对的变更轴：

| 变更来源 | 触及的方法 | 是不是 Exp 该管的轴 |
|---------|-----------|-------------------|
| 新工具类型 / 工具注册方式变 | `build_runtime` | ✅ 是（本轴） |
| system prompt 组装方式变 | [build_runtime:455](../matmaster/core/exp.py:455) | ✅ 基本是 |
| 一次 run 的取消/清理语义变 | `run_stream` | ⚠️ 另一根轴（运行时生命周期） |
| subagent 派生策略 / 事件转发变 | `_make_spawn_fn` | ❌ 另一根轴（跨 run 编排） |
| service 新增一个 runtime port | `build_runtime` 读 `ctx.runtime_ports` | ❌ 另一根轴（service 契约） |

**5 根轴里只有前 2 根是同一个理由，后 3 根是不同 actor。Exp 现在同时对约 4 个主人负责。**

逐簇判断：

- **簇 1（assemble + build_runtime）= 合理。** 标准 Builder/Factory，内聚高，正是最初设想的 Exp。唯一杂质是读 `ctx.runtime_ports` 里 service 塞的活 callable（约 20%），根因是上游 god-object，随契约拆分自然回正。
- **簇 2（run_stream 驱动 kernel）= 可辩护但颗粒度切错。** 实质内容只有 build + cancel 注入 + cleanup（RAII 式资源作用域），驱动 kernel 那段是纯透传。职责归属说得通，但 build+drive+cleanup 焊成一块，导致 devshell 没法复用、只能整段抄。
- **簇 3（_make_spawn_fn subagent 编排）= 最不该 Exp 管。** 事件重打标+多路复用、生命周期 hook 是流式/编排关注点，与装配无关。可保留的只有"子 agent 即另一次 Exp run"的递归本身。

**附带：三阶段 API 已名不副实。** `assemble()` 只剩 8 行且只被 `build_runtime` 调用；真实结构是"一个大相 + 一个薄 runner"，阶段边界不承重。

---

## 10. 残留物清单与潜在 bug

- ✅ **已删（Phase 0）** `Playground.agent` 及 attach/detach 中 `self.agent.session = ...` 镜像逻辑：全仓库 grep 确认仅测试赋过值（旧 `test_playground.py:313/336`，已随删），生产无人写——早期 Playground 直接持有 Agent 的化石。
- ✅ **已删（Phase 0）** `Playground._setup_session()`：零调用者，docstring 谎称被 `cleanup_bohrium_after_run` 调用（实际 cleanup 走 `SESSIONS` 字典还原）；连带删除它独占的 `_resolve_workspace_path()` 与转为只写的 `_prepare_metadata` 字段。
- 🪦 `PlaygroundContext.config_dir` / `env_vars`：声明了但生产路径基本无人消费或恒空；因已进入 `PlaygroundContext` 构造 / dump 形状，删除应放进契约重设阶段一并处理。
- ⚠️ `PlaygroundContext.cache_area`：当前 core 消费弱，但仍属于 Playground 物理环境快照的一部分，不建议作为第一批死代码删除。
- ⚠️ 潜在 bug（与本题无关）：[devshell/debug_run.py:48](../matmaster/devshell/debug_run.py:48) 从 `matmaster.devshell.cli` 导入 `_load_agents_general_llm`，但真实符号是 `matmaster.config.loader.load_agents_general_llm`（无下划线），按现状会 `ImportError`。

---

## 11. 边界重划与落地顺序

按 `refactor/context` 分支语境，边界方向仍是「物理环境」与「运行期装配」分离，但**实施顺序不应从契约手术打头**。更稳的路线是按风险递增、依赖在前推进：先缩小表面积，再统一运行生命周期，最后处理调用面更广的类型契约。

### Phase 0：死代码热身（低风险）✅ 已完成

清掉与生产路径脱钩的历史残留，缩小后续重构表面积。实际落地：

- 删除 `Playground.agent` 及 attach/detach 中同步 `agent.session` 的镜像逻辑。
- 删除 `Playground._setup_session()`；连带其独占的 `_resolve_workspace_path()` 与转为只写的 `_prepare_metadata` 字段（验证确认 `_setup_session` 无任何测试断言，故无断言可删）。
- 删除 `test_playground.py` 中仅有的两个写 `pg.agent` 的测试。
- 暂不删除 `cache_area`；`config_dir` / `env_vars` 这类已进入 `PlaygroundContext` schema 的字段，放到 Phase 3 随契约重设统一处理。

结果：68 行删除（2 文件），未碰 agent 执行链路、Bohrium、context 契约；`test_playground.py` 25 passed、`core`+`devshell` 539 passed。`debug_run.py` 的 `ImportError`（§10）是坏掉的 devshell 脚本而非 Playground 死代码，单独追踪、不并入本阶段。

### Phase 1：抽 RunScope / RuntimeScope（收益最高）

把 `Exp.run_stream()` 中的运行生命周期抽成可复用作用域：`build_runtime()`、session cancel token 注入、tool catalog cancel token 注入、`kernel.run_stream()` 调用、finally cleanup。

推荐形态：

```python
async with exp.runtime_scope(
    ctx,
    cancel_token=cancel_token,
    skill_resolver=skill_resolver,
    spawn_id=spawn_id,
) as runtime:
    async for event in runtime.kernel.run_stream(
        runtime.spec,
        task,
        history=history,
        cancel_token=cancel_token,
    ):
        yield event
```

完成后：

- `AgentRunService` 与 devshell 复用同一套 runtime lifecycle，不再手抄 cancel / cleanup。
- `Exp.run_stream()` 可退化为薄 wrapper，保持兼容但不再承载核心生命周期语义。
- 不动 `AgentKernel`，也不要求立刻拆 `PlaygroundContext`。

### Phase 2：抽 SubagentOrchestrator

在 Phase 1 已有 runtime scope 的基础上，把 `_make_spawn_fn` 的编排职责迁出 `Exp`：

- 子 `Exp` / 子 runtime 创建。
- `spawn_id` 生成。
- `SUBAGENT_START` / `SUBAGENT_STOP` hook 发射。
- child event 的 `source` / `spawn_id` 重打标。
- 通过 `child_event_forward_sink` 多路复用回父流。
- drain 子 run 并返回 final content。

迁移后 `Exp` 只负责装配子 runtime 所需资源，`AgentTool` 只持有 orchestrator 产出的 `spawn_fn`。`SubagentOrchestrator` 不应依赖 service 层 fanout，只依赖窄接口（runtime scope factory、hook executor、child event sink、exp config loader / child runtime factory）。

### Phase 3：重设契约对象（建议 1 + 2 合并）

这是仍需谨慎的一步，但不是从旧 `run_meta: dict[str, Any]` 大袋子开始拆。[run-meta-refactor 计划](superpowers/plans/2026-05-18-run-meta-refactor.md) 已在当前代码中落地：`RunMetadata` / `RunIdentity` 已是 typed + frozen，`PlaygroundRuntimePorts` 已窄化，`AgentRuntimeSpec.meta` 已拆成 `run_identity` + `turn_input`。因此 Phase 3 的风险主要集中在「不要把运行期装配输入重新做成新的 god-object / dict 袋子」以及 caller 迁移面；`RunMetadata` 瘦身反而是三刀里最小的一刀。仍建议放在 Phase 1 + Phase 2 之后推进，因为运行生命周期和 subagent 编排先稳定后，契约重设的 caller 面会更清晰。

建议按三刀切法：

1. **物理环境快照。** `Playground.prepare()` 只产出 `EnvironmentSnapshot` / `PlaygroundEnv`：`workdir`、`session_type`、`session_id`、`cache_area`、`execution_workdir`、`session`、`archival` 等物理执行环境事实。
2. **运行期装配输入。** 新增 `RuntimeAssemblyInput` / `AgentRunInput` / `AgentAssemblyContext`，承载 `llm_provider`、`llm_config`、`turn_input`、`user_instructions`、skill resolver / active skills、interaction 能力、figure upload、compaction ports 等由 service 解析出的运行期输入。避免命名为 `RunSpec`，以免与 `AgentRuntimeSpec` 混淆。
3. **RunMetadata 瘦身（局部小刀）。** `RunMetadata` 当前已经 typed + frozen，但仍混入 `turn_input`、`user_instructions`、`active_skills`、`bohrium_rebuild_events`。后续只需让它回到真正的被动身份 / 目录事实，例如 `run_dir`、`task_id`、`source`；其余字段分别迁到更贴近消费者的 typed 输入或专属 runtime/replay 状态。

约束：`RuntimePorts` 仍然是窄能力契约，只容纳 callable / sink / barrier / capability，不承接新的 `extra`、`metadata`、`state`、`context`、`services`、`payload` 或 `dict[str, Any]` 兜底袋子。

### Phase B：Bohrium session swap 专项（独立调查线）

Bohrium 不应混进 Phase 0 的死代码清理，也不应和 Phase 3 的契约手术绑在一个 PR。当前 Bohrium 路径绕过 `Playground.attach_ssh_session()` 可能有真实原因，包括：

- 保持 `/share` project-scoped 远端工作目录语义，避免自动拼 session 子目录。
- 先 `ssh_session.open()` 再注入 Bohrium runtime。
- 失败时需要同时回滚 `pg.session`、`pg._owns_session`、`SESSIONS` 与 runtime attach 状态。
- cleanup 需要恢复 original session，而不是简单 detach 当前 SSH session。

因此建议先做 read-only 调查，确认绕过原因，再决定是复用 / 修改 `attach_ssh_session()`，还是新增更贴近语义的 `swap_execution_session()` / `attach_execution_session()`。调查可提前并行，实际实现应独立成计划和 PR。

---

## 附录：对抗验证结论（8 条假设）

| # | 假设 | 结论 |
|---|------|------|
| 1 | 完整链路 PlaygroundManager→prepare→[service 注入]→Exp→run_stream→build_runtime→kernel.run_stream | ✅ confirmed |
| 2 | `llm_provider` 不由 prepare() 设，由 service `model_copy` 注入；类守边界但契约破边界 | ✅ confirmed |
| 3 | `build_runtime` 远不止组装：造 ToolRegistry/工具/prompt/topology/runner/scheduler/state/registry/compactor/assembler/kernel | ✅ confirmed |
| 4 | `run_stream` 实际驱动 kernel 执行，是执行驱动而非组装器 | ✅ confirmed |
| 5 | Exp 编排 subagent：造子 Exp、发 SUBAGENT_START/STOP、经 `child_event_forward_sink` 转发事件 | ✅ confirmed |
| 6 | 组装上下文职责已大体迁出 Exp 到 context/ 包 | ⚠️ **partial**（system prompt 仍在 Exp；当轮组装迁到 service） |
| 7 | `Playground.agent` 是 vestigial，生产无人写 | ✅ confirmed |
| 8 | AgentKernel 纯净，只把 `AgentRuntimeSpec` 作为唯一运行时配置对象，blur 集中在上游 | ✅ confirmed |
