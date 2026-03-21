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
**Plans**: TBD

Plans:
- [ ] 01-01: TBD
- [ ] 01-02: TBD

### Phase 2: Agent Kernel
**Goal**: Agent 执行循环只消费 AgentRuntimeSpec，不做 config 装配，可用 mock spec 独立测试
**Depends on**: Phase 1
**Requirements**: KERN-01, KERN-02, KERN-03, KERN-04, LLMP-01
**Success Criteria** (what must be TRUE):
  1. AgentKernel 用 mock AgentRuntimeSpec 可以完成 LLM call -> tool exec -> message accumulate -> loop 的完整循环
  2. 内置 loop detection 和 max turns guard 在触发条件下自动终止循环，不可被外部移除
  3. GuardPipeline 可以串联执行内置 guard + 外部注入的业务 guard，按顺序返回第一个拒绝结果
  4. Hook points (pre_tool_call/post_tool_call/pre_llm_call/should_continue) 可以被外部注入的 callable 扩展
  5. LLMProvider Protocol 实现的 chat() 和 chat_with_retry() 可以被 kernel 调用完成模型推理
**Plans**: TBD

Plans:
- [ ] 02-01: TBD
- [ ] 02-02: TBD

### Phase 3: Exp Assembly Layer
**Goal**: Exp 层消费 PlaygroundContext 输出 AgentRuntimeSpec，统一所有能力的装配路径
**Depends on**: Phase 1, Phase 2
**Requirements**: ASBL-01, ASBL-02, ASBL-03, ASBL-04, ASBL-05
**Success Criteria** (what must be TRUE):
  1. Exp base class 的 assemble() 方法可以接收 PlaygroundContext 并输出完整的 AgentRuntimeSpec
  2. ToolRegistry 可以在一个注册路径下统一管理 builtin tools、MCP tools 和 skill tools
  3. 业务 Guard（manuscript gate、auth failure gate）通过 assemble() 注入到 AgentRuntimeSpec.guards，kernel 无需感知业务语义
  4. Solver 模式（ResearchPlanner 等）作为 exp 层的高阶装配模式运行，不作为独立抽象层
  5. ContextBuilder 可以从 identity/skills/memory/task 多个来源组装出完整的 system prompt
**Plans**: TBD

Plans:
- [ ] 03-01: TBD
- [ ] 03-02: TBD

### Phase 4: Playground Layer
**Goal**: Playground 只负责环境准备，输出 PlaygroundContext，不穿透到 agent 层注册工具或配置 guard
**Depends on**: Phase 1
**Requirements**: WKSP-01, WKSP-02, WKSP-03
**Success Criteria** (what must be TRUE):
  1. 新 Playground base class 只暴露环境准备接口，输出 PlaygroundContext 类型化对象
  2. mat_master playground 重构后只输出 PlaygroundContext（含 session/workdir/MCP/config），不直接操作 agent 或 tool
  3. minimal playground 重构后只输出 PlaygroundContext，验证最简路径可用
**Plans**: TBD

Plans:
- [ ] 04-01: TBD

### Phase 5: Integration and Quality
**Goal**: mat_master 和 minimal 在新骨架上端到端跑通，三层契约有测试覆盖，迁移差异有文档记录
**Depends on**: Phase 2, Phase 3, Phase 4
**Requirements**: MIGR-01, MIGR-02, QUAL-01, QUAL-02, QUAL-03
**Success Criteria** (what must be TRUE):
  1. mat_master 在新三层管线（playground -> exp -> kernel）上可以端到端完成完整的 agent 运行流程
  2. minimal 在新三层管线上可以端到端完成完整的 agent 运行流程
  3. PlaygroundContext、AgentRuntimeSpec、AgentEvent 三个核心契约有单元测试覆盖其构造、验证和序列化行为
  4. mat_master 和 minimal 有端到端测试验证新旧路径的功能一致性
  5. 迁移文档清晰记录新旧架构差异和迁移步骤
**Plans**: TBD

Plans:
- [ ] 05-01: TBD
- [ ] 05-02: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5
Note: Phase 3 and Phase 4 can execute in parallel (both depend on Phase 1, communicate only through PlaygroundContext contract).

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation Contracts | 0/2 | Not started | - |
| 2. Agent Kernel | 0/2 | Not started | - |
| 3. Exp Assembly Layer | 0/2 | Not started | - |
| 4. Playground Layer | 0/1 | Not started | - |
| 5. Integration and Quality | 0/2 | Not started | - |
