# Phase 5: Integration and Quality - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

mat_master 和 minimal 在新骨架上端到端跑通（仅 direct 模式），三层契约有测试覆盖，上游场景对齐验证，迁移差异有文档记录。agent_run_service.py 重构为薄编排层：Playground.prepare() → Exp.assemble() → Kernel.run()。

Planner 模式不在 Phase 5 范围内——设计理念与重构方向冲突，后续重写。

</domain>

<decisions>
## Implementation Decisions

### 迁移切换策略
- **D-01:** 直接替换调用链，不用 feature flag。agent_run_service 直接改写为新管线，旧代码（evomaster/、playground/mat_master/）保留在磁盘但 service 层不再调用
- **D-02:** 旧代码标记废弃——在旧模块入口加 DeprecationWarning，保留但明确标记
- **D-03:** 只处理 mat_master + minimal 两种 playground_type。x_master 请求直接报错，不走旧路径兜底
- **D-04:** 保留轻量版预加载——启动时验证 config YAML 存在且可解析（关键字段齐全），不再动态导入旧 playground 模块

### PlannerExp / Solver 迁移范围
- **D-05:** Phase 5 只验证 direct 模式的完整流程。PlannerExp 不在范围内，后续重写
- **D-06:** 不对 planner 模式请求做特殊处理（不报错、不降级），上线时会同步更新新的 planner
- **D-07:** 全部 5 个 Hook 实现：ConfirmationHook、OutputProcessorHook（auto_save + summarize）、SkillHitHook、AssistantStateHook
- **D-08:** 业务 Hook 独立为 `matmaster/hooks/` 包，按职责分文件（confirmation.py、output_processor.py、skill_hit.py、assistant_state.py）。EventEmitterHook 留在 engine/hooks.py（通用事件桥梁）

### 端到端测试方法
- **D-09:** 直接调用真实 LLM API 测试（使用 config.yaml 中的 API 配置），通过 CLI 模式触发。不录制/回放
- **D-10:** 外部依赖分层测试：单元测试全 mock（Redis/Bohrium/OSS），单独一组 integration test 需要真实环境才能跑（CI 中跳过，手动触发）
- **D-11:** E2E 验收标准：管线连通性 + 功能对齐（确认交互、事件持久化、workspace 上传触发等），但不验证具体 tool 调用结果

### Service 层重构深度
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

</decisions>

<specifics>
## Specific Ideas

- event_callback 闭包（130 行）是重构核心——用 EventRouter + 3 Handler 完全替代，消除闭包变量 hack（`list[...]` 模拟可变变量）
- _should_persist_event 和 _should_skip_push 的过滤规则可直接迁移到 PersistenceHandler 和 SSEHandler，逻辑不变
- run_agent_sync 从 ~490 行压缩到 ~200 行，核心流程清晰：Playground.prepare() → Bohrium setup → Exp.assemble() → 历史加载 → EventRouter 启动 → Kernel.run() → 后处理
- WorkspaceHandler 将 _ssh_attached、_last_workspace_snapshot、_last_workspace_check_time 从闭包变量转为实例字段

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 项目定义
- `.planning/PROJECT.md` — 项目愿景、核心价值、方案选型
- `.planning/REQUIREMENTS.md` — Phase 5 需求：MIGR-01, MIGR-02, QUAL-01 ~ QUAL-05
- `.planning/ROADMAP.md` — Phase 5 目标、成功标准、依赖关系

### 迁移映射（Phase 5 核心参考）
- `.planning/phases/05-integration-quality/MIGRATION-MAPPING.md` — agent_run_service.py 逐行迁移映射表，6 阶段分解，新增组件列表，迁移后完整伪代码

### Phase 1-4 交付物（Phase 5 直接依赖）
- `matmaster/types/context.py` — PlaygroundContext 定义（含 WorkspaceArchivalConfig）
- `matmaster/types/runtime.py` — AgentRuntimeSpec 定义
- `matmaster/types/events.py` — 16 种事件类型（AgentEvent + SystemEvent）
- `matmaster/engine/agent.py` — AgentKernel.run()（需扩展 history 参数）
- `matmaster/engine/hooks.py` — Hook Protocol、BaseHook、EventEmitterHook
- `matmaster/engine/guard_pipeline.py` — GuardPipeline + LoopDetectionGuard
- `matmaster/engine/types.py` — Message 层级（SystemMessage/UserMessage/AssistantMessage/ToolMessage）
- `matmaster/assembly/exp.py` — Exp base class（assemble/run 框架）
- `matmaster/assembly/direct_exp.py` — DirectExp（能力装配，MCP/Skill 自行初始化）
- `matmaster/assembly/tool_registry.py` — ToolRegistry（扁平注册表）
- `matmaster/assembly/context_builder.py` — ContextBuilder（分段 system prompt）
- `matmaster/playground/playground.py` — Playground（workspace/session/logging）
- `matmaster/bus/queue.py` — MessageBus（同步 queue）
- `matmaster/bus/bridge.py` — QueueBridge（BusEvent → SSE payload）

### Phase 1-4 上下文
- `.planning/phases/01-foundation-contracts/01-CONTEXT.md` — 事件设计、Guard 接口、MessageBus 消费模式
- `.planning/phases/02-agent-kernel/02-CONTEXT.md` — 循环终止、Hook 扩展、LLMProvider 边界
- `.planning/phases/03-exp-assembly-layer/03-CONTEXT.md` — ToolRegistry、ContextBuilder、DirectExp、WorkerRegistry Protocol
- `.planning/phases/04-playground-layer/04-CONTEXT.md` — Playground 职责、PlaygroundContext 字段修正、Exp MCP/Skill 初始化

### 现有 Service 层（重构目标）
- `src/services/agent_run_service.py` — 当前编排层（~820 行），Phase 5 重构为薄编排层
- `src/services/stream_service.py` — SSE streaming + ReplyQueue，Phase 5 不改动
- `src/services/chat_history.py` — ChatHistoryConverter，需新增 events_to_messages() 方法
- `src/services/agent_run_bohrium.py` — Bohrium 节点生命周期，包装为 BohriumSetupService
- `src/services/quota_service.py` — 配额扣减，接口不变
- `src/services/worker_registry_service.py` — Redis WorkerRegistry 实现，接口不变

### 配置文件
- `configs/mat_master/config.yaml` — mat_master 完整配置（LLM/agents/MCP/skills/session/playground）
- `configs/minimal/config.yaml` — minimal 配置

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `matmaster/engine/hooks.py` EventEmitterHook — kernel→bus 桥梁，Phase 5 新 Hook 需遵循同一 Hook Protocol
- `matmaster/bus/bridge.py` QueueBridge — 已实现 16 种事件到 SSE payload 的映射，EventRouter.SSEHandler 可复用
- `src/services/agent_run_service.py` L80-99 的 `_should_persist_event()` / `_should_skip_push()` — 过滤规则可直接迁移到 PersistenceHandler / SSEHandler
- `src/services/agent_run_service.py` L247-311 的 workspace 快照/上传逻辑 — 整体迁移到 WorkspaceHandler
- `src/services/agent_run_bohrium.py` 四个函数 — 包装为 BohriumSetupService（setup + cleanup 两段）
- `src/services/chat_history.py` ChatHistoryConverter — 已有 events_to_dialog_messages()，需新增 events_to_messages() 返回 matmaster Message 类型

### Established Patterns
- Pydantic frozen model 用于不可变契约
- `@runtime_checkable` Protocol 用于接口定义
- 同步 threading 模型 — agent 运行在 ThreadPoolExecutor 中
- 事件通过 MessageBus emit → QueueBridge 消费 → SSE payload
- send_cb 支持同步/异步双模式（L394-406 的 asyncio.run_coroutine_threadsafe 逻辑）

### Integration Points
- `matmaster/hooks/` — Phase 5 新建的业务 Hook 包
- AgentKernel.run() — 需扩展 history: list[Message] | None 参数
- agent_run_service.run_agent_sync() — 整体重写，方法签名不变
- ChatHistoryConverter — 新增方法，旧方法保留兼容
- EventRouter — Phase 5 新组件，run_agent_sync 内部创建和管理生命周期

</code_context>

<deferred>
## Deferred Ideas

- PlannerExp 完全重写 — 设计理念与重构方向冲突，独立排期
- x_master playground 迁移 — 优先 mat_master 和 minimal
- Session Protocol 抽象 — Phase 4 延迟项
- Context compaction 集成 — CompactionConfig 已在 spec 中，具体策略留后续
- 工具并行执行 — Phase 2 延迟项
- 旧代码清理（删除 evomaster/playground/mat_master 中的废弃模块）— 迁移稳定后再处理

</deferred>

---

*Phase: 05-integration-quality*
*Context gathered: 2026-03-22*
