# Requirements: MatMaster 框架重构 (v2)

**Defined:** 2026-03-21
**Core Value:** 三层抽象（playground→exp→agent）必须具有清晰、稳定、可测试的职责边界

## v1 Requirements

### Contracts (契约层)

- [x] **CONT-01**: PlaygroundContext 使用 Pydantic frozen model 定义，包含 workdir、session type、cache area、环境变量、MCP manager、skill registry
- [x] **CONT-02**: AgentRuntimeSpec 使用 Pydantic frozen model 定义，包含 LLM provider、tool registry、guards、termination policy、hooks、compaction config
- [x] **CONT-03**: AgentEvent 使用 Pydantic discriminated union 定义事件类型（ThoughtEvent/ToolCallEvent/ToolResultEvent/FinishEvent/ErrorEvent）
- [x] **CONT-04**: Guard Protocol 接口定义（evaluate 方法签名 + GuardResult 返回类型）
- [x] **CONT-05**: TerminationPolicy 类型定义（max_turns/finish_tool/stop_event 的统一抽象）

### Kernel (执行核心)

- [ ] **KERN-01**: AgentKernel 实现纯执行循环，只消费 AgentRuntimeSpec，不做 config 装配
- [ ] **KERN-02**: 内置通用 Guard（loop detection、max turns），不可移除
- [ ] **KERN-03**: GuardPipeline 支持串联执行多个 Guard（内置 + 业务注入）
- [ ] **KERN-04**: Hook Point API 支持 pre_tool_call/post_tool_call/pre_llm_call/should_continue 扩展点

### Assembly (Exp 装配层)

- [ ] **ASBL-01**: Exp base class 定义 assemble() 方法，消费 PlaygroundContext 输出 AgentRuntimeSpec
- [ ] **ASBL-02**: ToolRegistry 统一 builtin tools、MCP tools、skill tools 的注册路径
- [ ] **ASBL-03**: 业务 Guard（manuscript gate、auth failure gate）通过 AgentRuntimeSpec.guards 注入
- [ ] **ASBL-04**: Solver 模式（ResearchPlanner 等）收入 exp 层作为高阶装配模式
- [ ] **ASBL-05**: ContextBuilder 从 identity/skills/memory/task 多源组装 system prompt

### EventBus (事件系统)

- [ ] **EBUS-01**: MessageBus 使用同步 queue 实现，适配 ThreadPoolExecutor 线程模型
- [ ] **EBUS-02**: QueueBridge 将 MessageBus 事件桥接到现有 SSE 消费路径

### LLM Provider

- [ ] **LLMP-01**: LLMProvider Protocol 接口定义 chat() + chat_with_retry() + streaming 统一签名

### Workspace (Playground 层)

- [ ] **WKSP-01**: 新 Playground base class 只负责环境准备，输出 PlaygroundContext
- [ ] **WKSP-02**: mat_master playground 重构为只输出 PlaygroundContext
- [ ] **WKSP-03**: minimal playground 重构为只输出 PlaygroundContext

### Migration (迁移)

- [ ] **MIGR-01**: mat_master 在新骨架上端到端跑通完整流程
- [ ] **MIGR-02**: minimal 在新骨架上端到端跑通完整流程

### Quality (质量)

- [ ] **QUAL-01**: 三层契约（PlaygroundContext/AgentRuntimeSpec/AgentEvent）有单元测试覆盖
- [ ] **QUAL-02**: mat_master 和 minimal 有端到端测试验证迁移正确性
- [ ] **QUAL-03**: 迁移文档记录新旧架构差异和迁移指南

## v2 Requirements

### LLM Provider (精细化)

- **LLMP-02**: OpenAI/Anthropic/Google Provider 独立实现优化
- **LLMP-03**: reasoning_protocol/temperature_policy 等领域特定精细控制

### Migration (完善)

- **MIGR-03**: CompatAdapter（Strangler Fig 模式）桥接旧 playground 到新契约
- **MIGR-04**: 旧继承链清理（StreamingMatMasterAgent、旧 BaseExp 等）

### Observability

- **OBSV-01**: 通过 MessageBus 接入 OpenTelemetry/结构化日志
- **OBSV-02**: Agent 执行 timeline 指标（turn duration、tool/LLM latency）

## Out of Scope

| Feature | Reason |
|---------|--------|
| Multi-Agent 编排引擎 | 先完成单 agent 解耦，编排后续设计 |
| src/ Web Service 层重构 | 保持现状，不在本次范围 |
| x_master playground 迁移 | 优先 mat_master 和 minimal |
| 前端 UI 改动 | 本次只涉及后端框架层 |
| 配置热更新 | frozen spec + 重新创建实例替代 |
| 多租户隔离 | 属于 Service 层运维能力 |
| Agent 内置 Memory/Knowledge Store | kernel 只管 in-context messages |
| Tool 执行沙箱 | Session 类型决定隔离级别 |
| 前端协议内置 (ag-ui/SSE) | agent 不应知道传输协议 |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| CONT-01 | Phase 1 | Complete |
| CONT-02 | Phase 1 | Complete |
| CONT-03 | Phase 1 | Complete |
| CONT-04 | Phase 1 | Complete |
| CONT-05 | Phase 1 | Complete |
| KERN-01 | Phase 2 | Pending |
| KERN-02 | Phase 2 | Pending |
| KERN-03 | Phase 2 | Pending |
| KERN-04 | Phase 2 | Pending |
| ASBL-01 | Phase 3 | Pending |
| ASBL-02 | Phase 3 | Pending |
| ASBL-03 | Phase 3 | Pending |
| ASBL-04 | Phase 3 | Pending |
| ASBL-05 | Phase 3 | Pending |
| EBUS-01 | Phase 1 | Pending |
| EBUS-02 | Phase 1 | Pending |
| LLMP-01 | Phase 2 | Pending |
| WKSP-01 | Phase 4 | Pending |
| WKSP-02 | Phase 4 | Pending |
| WKSP-03 | Phase 4 | Pending |
| MIGR-01 | Phase 5 | Pending |
| MIGR-02 | Phase 5 | Pending |
| QUAL-01 | Phase 5 | Pending |
| QUAL-02 | Phase 5 | Pending |
| QUAL-03 | Phase 5 | Pending |

**Coverage:**
- v1 requirements: 25 total
- Mapped to phases: 25
- Unmapped: 0

---
*Requirements defined: 2026-03-21*
*Last updated: 2026-03-21 after roadmap creation*
