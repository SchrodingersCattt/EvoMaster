# Phase 3: Exp Assembly Layer - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

实现能力装配层：Exp base class 消费 PlaygroundContext 输出 AgentRuntimeSpec，统一 tool/guard/prompt 的注册与组装路径。交付 Exp base class + ToolRegistry + ContextBuilder + 一个可工作的 DirectExp 子类验证完整装配流程。

**ASBL-06 范围调整**：Phase 3 只定义 WorkerRegistry Protocol 和注入点（PlaygroundContext.run_meta），不实际迁移业务代码。实际的 WorkerRegistry/Bohrium/run_interrupted 业务逻辑迁移在 Phase 5 集成阶段完成。

</domain>

<decisions>
## Implementation Decisions

### ToolRegistry 统一注册
- 扁平注册表模型：所有工具统一注册到同一命名空间，通过 source 标签区分来源（builtin/mcp/skill）
- 同名工具冲突处理：后注册覆盖前者，assemble() 按顺序注册 builtin → MCP → skill，日志警告覆盖事件
- MCP 工具集成方式：装配时拉取——assemble() 时连接 MCP server，拉取 tool list，包装成 Tool 对象注册。工具列表在 spec 构建时确定，kernel 看到完整工具集
- Tool 统一接口：定义 Tool Protocol（name + json_schema + execute），参考 nanobot 设计。builtin/MCP/skill 各自实现 Protocol，kernel 只看到统一接口

### ContextBuilder 组装
- 分段 Builder 模式：拆分为多个段（identity/mode_contract/skills/tools/memory/task），每段独立生成，最后拼接
- 固定段顺序：identity → mode_contract → skills → tools → memory → task。顺序固定，每段可选启用/禁用。LLM 对 prompt 开头的指令权重更高，所以 identity 放最前
- direct/planner 模式差异：通过 mode_contract 段切换。identity/skills/tools/memory 段共用，只有 mode_contract 和 task 段根据模式变化
- skills 段生成：参考 nanobot 实现，ContextBuilder 接收 SkillRegistry 引用自己遍历生成 skills 段（每个 skill 的 name + description + tool list）

### Solver 收编方式
- Solver 就是 Exp 子类：不同子类通过不同的 assemble() 策略（选择不同的 prompt、tool 集、guard 配置）适配不同任务类型
- Phase 3 交付范围：Exp base class + assemble() 框架 + 一个可工作的 DirectExp 子类。PlannerExp 留给后续迭代完全重构
- assemble() 设计为可重复调用：每次用不同参数产生不同 spec，为 PlannerExp 预留能力（per-step 重新 assemble 切换 prompt/tools）
- PlannerExp 可完全 override run() 实现多步状态机

### WorkerRegistry 边界
- run_interrupted 检测保留在 Service 层（stream_service.py）：因为触发在 SSE subscribe 时，与 agent run 无关
- Bohrium 凭证加载保留在 Service 层：涉及 DB + 外部 API 调用，通过 PlaygroundContext.run_meta 传递给 Exp
- Bohrium 凭证绑定到 session 在 Exp 层：Exp 的 assemble() 或 run() 中完成
- session_run_owner 管理：定义 WorkerRegistry Protocol（set/refresh/delete），Service 层提供 Redis 实现，通过依赖注入传入 Exp。Phase 3 只定义接口，Phase 5 实际接入
- Phase 3 只建接口不迁移业务代码，Phase 5 统一迁移。ROADMAP 需同步更新 ASBL-06 的范围描述

### Claude's Discretion
- Tool Protocol 中 execute() 的具体签名（同步 vs 异步，参数类型）
- ContextBuilder 各段的具体文本格式和分隔符
- DirectExp 的具体 assemble() 实现细节
- ToolRegistry 内部存储结构

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 项目定义
- `.planning/PROJECT.md` -- 项目愿景、核心价值、方案选型、nanobot 参考架构
- `.planning/REQUIREMENTS.md` -- Phase 3 需求：ASBL-01 ~ ASBL-06（注意 ASBL-06 范围调整）
- `.planning/ROADMAP.md` -- Phase 3 目标、成功标准、依赖关系

### Phase 1 & 2 交付物（Phase 3 直接依赖）
- `matmaster/types/runtime.py` -- AgentRuntimeSpec 定义（tool_registry 字段需从 Any 更新为 ToolRegistry Protocol）
- `matmaster/types/context.py` -- PlaygroundContext 定义（run_meta 字段传递业务元数据）
- `matmaster/types/guards.py` -- Guard Protocol、GuardContext、GuardResult 定义
- `matmaster/types/events.py` -- AgentEvent/SystemEvent/BusEvent discriminated union
- `matmaster/engine/agent.py` -- AgentKernel 执行循环（Phase 3 输出的 AgentRuntimeSpec 的消费者）
- `matmaster/engine/hooks.py` -- Hook Protocol、BaseHook、EventEmitterHook
- `matmaster/engine/guard_pipeline.py` -- GuardPipeline（内置 LoopDetectionGuard）
- `matmaster/bus/queue.py` -- MessageBus 同步事件总线

### Phase 1 & 2 上下文
- `.planning/phases/01-foundation-contracts/01-CONTEXT.md` -- Phase 1 决策（matmaster/ 目录、事件设计、Guard 接口、MessageBus 消费模式）
- `.planning/phases/02-agent-kernel/02-CONTEXT.md` -- Phase 2 决策（循环终止、Hook 扩展、LLMProvider 边界、Guard 拦截反馈）

### 代码库分析
- `.planning/codebase/ARCHITECTURE.md` -- 现有架构、数据流、ThreadPoolExecutor 线程模型
- `.planning/codebase/CONVENTIONS.md` -- 命名规范、代码风格、Protocol 使用模式
- `.planning/codebase/STRUCTURE.md` -- 目录结构、新代码放置位置

### 现有实现（需理解以设计 assembly 层）
- `evomaster/core/exp.py` -- 现有 BaseExp（agent + config + run + save_results）
- `playground/mat_master/core/registry.py` -- 现有 MatMasterSkillRegistry（4 层优先级）
- `playground/mat_master/core/async_tool_registry.py` -- 现有 AsyncToolRegistry（MCP 工具分类）
- `playground/mat_master/prompts/build_prompt.py` -- 现有 prompt 组装（compose_mat_master_system_prompt）
- `playground/mat_master/core/solvers/research_planner.py` -- 现有 ResearchPlanner solver（状态机 + 多步执行）
- `playground/mat_master/core/agent.py` -- 现有 MatMasterAgent（tool guard + async execution + job registry）
- `playground/mat_master/core/tool_guard.py` -- 现有 ToolGuard（6 关注点 guard 实现）

### 业务集成（Phase 5 迁移参考）
- `src/services/worker_registry_service.py` -- WorkerRegistry 当前实现（Redis-backed session_run_owner）
- `src/services/agent_run_bohrium.py` -- Bohrium 凭证加载 + 节点生命周期
- `src/services/agent_run_service.py` -- 当前编排层（Phase 5 简化为薄编排层）
- `src/services/stream_service.py` -- run_interrupted 检测 + RedisReplyQueue
- `src/services/deploy_state_service.py` -- version 分类（deploy vs restart）

### 参考架构
- nanobot kernel (`/Users/kealdoom/Desktop/github/nanobot/nanobot/`) -- ToolRegistry + ContextBuilder + AgentLoop 设计参考

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `matmaster/types/runtime.py` AgentRuntimeSpec -- Phase 3 输出契约，需更新 tool_registry 字段类型为 Tool Protocol list 或 ToolRegistry Protocol
- `matmaster/types/guards.py` Guard Protocol -- 业务 guard 注入的类型约束，直接复用
- `matmaster/engine/hooks.py` Hook/BaseHook/EventEmitterHook -- Exp 层注入 EventEmitterHook 到 spec.hooks，直接复用
- `matmaster/bus/queue.py` MessageBus -- EventEmitterHook 使用，Exp 层创建并注入
- `matmaster/providers/openai_provider.py` OpenAIProvider -- DirectExp 装配时使用的具体 LLMProvider

### Established Patterns
- Pydantic frozen model 用于不可变契约（AgentRuntimeSpec、PlaygroundContext）
- `@runtime_checkable` Protocol 用于接口定义（Guard、Hook、LLMProvider）-- Tool Protocol 应沿用
- 同步 threading 模型 -- agent 运行在 ThreadPoolExecutor 中，Tool.execute() 应为同步方法
- 事件通过 MessageBus emit -- Exp 层创建 MessageBus + EventEmitterHook 注入到 spec

### Integration Points
- `matmaster/assembly/` -- Phase 3 新代码位置（Exp base、ToolRegistry、ContextBuilder、DirectExp）
- AgentRuntimeSpec -- Exp 层输出、kernel 层输入。Phase 3 需要更新 tool_registry 字段类型
- PlaygroundContext.run_meta -- Service 层传递 Bohrium 凭证等业务元数据的通道
- Phase 4 (Playground) 将构建 PlaygroundContext 传递给 Exp.assemble()
- Phase 5 (Integration) 将实际迁移 WorkerRegistry/Bohrium 业务逻辑到新接口

</code_context>

<specifics>
## Specific Ideas

- 参考 nanobot 的 ToolRegistry + ContextBuilder 设计，但适配 matmaster 的同步 threading 模型
- assemble() 可重复调用是有意预留：PlannerExp 在每个 step 前可以用不同参数重新 assemble（切换 prompt、启用/禁用特定工具）
- Phase 3 用 mock 验证装配流程：DirectExp + mock PlaygroundContext + 真实 ToolRegistry/ContextBuilder → AgentRuntimeSpec → AgentKernel.run()
- ASBL-06 范围调整需要同步更新 ROADMAP.md 和 REQUIREMENTS.md

</specifics>

<deferred>
## Deferred Ideas

- PlannerExp 完全重构 -- 后续迭代，Phase 3 只建 base + DirectExp
- WorkerRegistry/Bohrium 业务逻辑实际迁移 -- Phase 5
- 工具并行执行 -- Phase 2 已延迟，继续延迟
- Context compaction 集成 -- CompactionConfig 已在 spec 中，具体策略留后续

</deferred>

---

*Phase: 03-exp-assembly-layer*
*Context gathered: 2026-03-22*
