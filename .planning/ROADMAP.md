# Roadmap: MatMaster Framework Refactoring (v2)

## Overview

本次重构将 matmaster 的 playground/exp/agent 三层架构从继承驱动改为契约驱动。从类型化契约出发，构建纯执行 kernel，然后分别重构 exp 装配层和 playground 环境层，最终在新骨架上完成 mat_master 和 minimal 的端到端迁移与质量验证。

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Foundation Contracts** - 定义三层边界契约、事件类型和 EventBus，建立后续所有组件的类型基础
- [ ] **Phase 2: Agent Kernel** - 实现纯执行循环和 LLM Provider 抽象，交付可独立测试的 kernel
- [ ] **Phase 3: Exp Assembly Layer** - 实现能力装配层，统一 tool/guard/prompt/solver 的注册与组装路径
- [ ] **Phase 4: Playground Layer** - 重构 playground 为纯环境准备层，只输出 PlaygroundContext
- [ ] **Phase 5: Integration and Quality** - mat_master/minimal 端到端迁移验证，三层契约测试覆盖，迁移文档

## Phase Details

### Phase 1: Foundation Contracts
**Goal**: 三层间通信有稳定的类型化契约，事件系统有统一的发射和消费路径
**Depends on**: Nothing (first phase)
**Requirements**: CONT-01, CONT-02, CONT-03, CONT-04, CONT-05, EBUS-01, EBUS-02
**Success Criteria** (what must be TRUE):
  1. PlaygroundContext 和 AgentRuntimeSpec 可以通过 Pydantic 实例化并通过 frozen 验证（不可变性）
  2. AgentEvent discriminated union 可以正确序列化/反序列化所有 5 种事件类型
  3. Guard Protocol 和 TerminationPolicy 有明确的类型签名，mypy 可以对实现者做静态检查
  4. MessageBus 可以在同步线程中发射事件，消费端按 FIFO 顺序接收
  5. QueueBridge 可以将 MessageBus 事件桥接到现有 SSE 消费路径
**Plans:** 2 plans

Plans:
- [ ] 01-01-PLAN.md -- 定义所有契约类型：Guard Protocol、PlaygroundContext、AgentRuntimeSpec、AgentEvent/SystemEvent/BusEvent (CONT-01~05)
- [ ] 01-02-PLAN.md -- 实现 MessageBus 同步事件总线和 QueueBridge SSE 桥接适配器 (EBUS-01~02)

### Phase 2: Agent Kernel
**Goal**: Agent 执行循环只消费 AgentRuntimeSpec，不做 config 装配，可用 mock spec 独立测试
**Depends on**: Phase 1
**Requirements**: KERN-01, KERN-02, KERN-03, KERN-04, LLMP-01
**Success Criteria** (what must be TRUE):
  1. AgentKernel 用 mock AgentRuntimeSpec 可以完成 LLM call -> tool exec -> message accumulate -> loop 的完整循环
  2. 内置 loop detection 和 max turns guard 在触发条件下自动终止循环，不可被外部移除
  3. GuardPipeline 可以串联执行内置 guard + 外部注入的业务 guard，按顺序返回第一个拒绝结果
  4. Hook points (pre_tool_call/post_tool_call/pre_llm_call/should_continue) 可以被外部注入的 callable 扩展
  5. LLMProvider Protocol 实现的 chat() + chat_with_retry() + chat_stream() 可以被 kernel 调用完成模型推理
**Plans:** 3 plans

Plans:
- [ ] 02-01-PLAN.md -- Kernel 基础模块：消息类型、LLMProvider Protocol、Hook 系统、GuardPipeline (KERN-02, KERN-03, KERN-04, LLMP-01)
- [ ] 02-02-PLAN.md -- AgentKernel 纯执行循环 + AgentRuntimeSpec 类型更新 + 公开 API (KERN-01, KERN-04)
- [ ] 02-03-PLAN.md -- Gap closure: 添加 chat_with_retry() 到 LLMProvider Protocol 并在 OpenAIProvider 实现显式重试逻辑 (LLMP-01)

### Phase 3: Exp Assembly Layer
**Goal**: Exp 层消费 PlaygroundContext 输出 AgentRuntimeSpec，统一所有能力的装配路径，集成跨 pod 协调
**Depends on**: Phase 1, Phase 2
**Requirements**: ASBL-01, ASBL-02, ASBL-03, ASBL-04, ASBL-05, ASBL-06
**Success Criteria** (what must be TRUE):
  1. Exp base class 的 assemble() 方法可以接收 PlaygroundContext 并输出完整的 AgentRuntimeSpec
  2. ToolRegistry 可以在一个注册路径下统一管理 builtin tools、MCP tools 和 skill tools
  3. 业务 Guard（manuscript gate、auth failure gate）通过 assemble() 注入到 AgentRuntimeSpec.guards，kernel 无需感知业务语义
  4. Solver 模式（ResearchPlanner 等）作为 exp 层的高阶装配模式运行，不作为独立抽象层
  5. ContextBuilder 可以从 identity/skills/memory/task 多个来源组装出完整的 system prompt
  6. WorkerRegistry Protocol 和注入点已定义（PlaygroundContext.run_meta 传递凭证、Protocol 抽象 session_run_owner 管理），Phase 3 不实际迁移业务代码，实际的 WorkerRegistry/Bohrium/run_interrupted 业务逻辑迁移在 Phase 5 完成
**Plans:** 4 plans

Plans:
- [ ] 03-01-PLAN.md -- Tool Protocol + ToolRegistry 统一注册 + AgentRuntimeSpec 类型更新 (ASBL-02)
- [ ] 03-02-PLAN.md -- ContextBuilder 分段 system prompt 组装 + WorkerRegistry Protocol 定义 (ASBL-05, ASBL-06)
- [ ] 03-03-PLAN.md -- Exp base class + DirectExp 子类 + 业务 Guard 注入 + 包导出 (ASBL-01, ASBL-03, ASBL-04)
- [ ] 03-04-PLAN.md -- Gap closure: 修复 assembly/engine 循环导入，恢复 engine 测试套件 (ASBL-01, ASBL-03, ASBL-04)

### Phase 4: Playground Layer
**Goal**: Playground 只负责物理环境准备（workspace/session/logging），输出 PlaygroundContext（含 workspace 归档配置）。MCP/skill/tool 等能力初始化由 Exp 层负责，config 分发由 Service 层编排
**Depends on**: Phase 1, Phase 3 (修正 PlaygroundContext 字段归属和 DirectExp 传参路径)
**Requirements**: WKSP-01, WKSP-02, WKSP-03, WKSP-04
**Success Criteria** (what must be TRUE):
  1. 统一 Playground 类只暴露 prepare(run_meta)->PlaygroundContext + cleanup() 接口，只处理 workspace 目录、Session（Docker/SSH/Local）、logging
  2. PlaygroundContext 移除 mcp_manager 和 skill_registry 字段（能力初始化由 Exp 层负责），新增 WorkspaceArchivalConfig 嵌套字段
  3. mat_master 和 minimal 通过同一个 Playground 类 + 不同 config YAML 驱动，验证两种配置路径可用
  4. DirectExp 构造函数接收 mcp_config/skill_config，assemble() 中自行初始化 MCP 和 Skill（不再从 PlaygroundContext 读取）
  5. PlaygroundContext 包含 workspace 归档配置（WorkspaceArchivalConfig），run 结束后可通过配置驱动 workspace 快照上传
**Plans**: 3 plans

Plans:
- [ ] 04-01-PLAN.md -- PlaygroundContext 收缩为物理环境契约 + WorkspaceArchivalConfig + 统一 Playground 核心生命周期 (WKSP-01, WKSP-04)
- [ ] 04-02-PLAN.md -- 用统一 Playground 跑通 mat_master / minimal 两条 config 路径，并补 archival config (WKSP-02, WKSP-03)
- [ ] 04-03-PLAN.md -- DirectExp 自行初始化 MCP / Skill 并通过 cleanup finally 自管资源，不再依赖 PlaygroundContext 能力字段 (WKSP-01)

### Phase 5: Integration and Quality
**Goal**: mat_master 和 minimal 在新骨架上端到端跑通，三层契约有测试覆盖，上游场景对齐验证，迁移差异有文档记录
**Depends on**: Phase 2, Phase 3, Phase 4
**Requirements**: MIGR-01, MIGR-02, QUAL-01, QUAL-02, QUAL-03, QUAL-04, QUAL-05
**Success Criteria** (what must be TRUE):
  1. mat_master 在新三层管线（playground -> exp -> kernel）上可以端到端完成完整的 agent 运行流程
  2. minimal 在新三层管线上可以端到端完成完整的 agent 运行流程
  3. PlaygroundContext、AgentRuntimeSpec、AgentEvent 三个核心契约有单元测试覆盖其构造、验证和序列化行为
  4. mat_master 和 minimal 有端到端测试验证新旧路径的功能一致性
  5. 迁移文档清晰记录新旧架构差异和迁移步骤
  6. 上游场景端到端验证通过：run_interrupted 检测（deploy vs restart）、跨 pod 订阅恢复（RedisReplyQueue 跨 worker 确认）、workspace OSS 上传、Bohrium 节点生命周期（创建/复用/清理）
  7. 配额扣减（use_quota）在新管线中正确执行——run 成功后扣减、失败不扣减
  8. agent_run_service.py 简化为薄编排层：接收请求 -> Playground.prepare() 输出 PlaygroundContext -> Exp.assemble() 输出 AgentRuntimeSpec -> Kernel.run() 执行 -> 返回结果，不再承担装配、事件过滤、workspace 上传等职责
**Plans**: 5 plans

Plans:
- [x] 05-01-PLAN.md -- AgentKernel history 扩展 + PlaygroundContext.with_bohrium() + 4 个业务 Hook 实现 (MIGR-01, MIGR-02)
- [x] 05-02-PLAN.md -- EventRouter + PersistenceHandler/SSEHandler/WorkspaceHandler + BohriumSetupService (MIGR-01, QUAL-04)
- [x] 05-03-PLAN.md -- agent_run_service.py 重写为新管线 + ChatHistoryConverter 扩展 + DirectExp hooks 合并 (MIGR-01, MIGR-02, QUAL-05)
- [ ] 05-04-PLAN.md -- 三层契约边界测试 + E2E 管线测试 + 上游场景测试 + 配额管线测试 (QUAL-01, QUAL-02, QUAL-04, QUAL-05)
- [ ] 05-05-PLAN.md -- 迁移文档：新旧架构差异、替换组件映射、配置变更、迁移指南 (QUAL-03)

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5
Note: Phase 4 now depends on Phase 3 (修正 PlaygroundContext 字段归属和 DirectExp 传参路径), cannot execute in parallel.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation Contracts | 2/2 | Complete | 2026-03-21 |
| 2. Agent Kernel | 2/3 | Gap closure | - |
| 3. Exp Assembly Layer | 3/4 | Gap closure | - |
| 4. Playground Layer | 0/3 | Not started | - |
| 5. Integration and Quality | 0/5 | Not started | - |
