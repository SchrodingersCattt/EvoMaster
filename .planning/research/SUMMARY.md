# Project Research Summary

**Project:** MatMaster Agent Framework Refactoring (v2)
**Domain:** AI Agent Framework Kernel -- Brownfield Refactoring (playground/exp/agent three-layer architecture)
**Researched:** 2026-03-21
**Confidence:** HIGH

## Executive Summary

MatMaster 是一个面向科研场景的 AI Agent 框架，当前的核心问题是 playground/exp/agent 三层抽象之间的职责耦合：playground 层穿透到 agent 层注册工具，agent 层通过继承链扩展行为（MatMasterAgent -> Agent -> BaseAgent），配置以 `Dict[str, Any]` 形式在层间隐式传递。研究结论是：通过引入两个类型化契约（PlaygroundContext 和 AgentRuntimeSpec）作为层间通信的唯一通道，用 Pydantic frozen model 定义边界数据，用 typing.Protocol 定义组件接口，将当前的继承驱动架构重构为依赖注入驱动架构。参考 nanobot kernel 的极简 loop 设计，但适配 matmaster 的 brownfield 现实。

技术栈方面，重构的核心策略是"零新依赖"。现有的 Python 3.13 + Pydantic v2 + 原生 LLM SDK + FastAPI 完全足以支撑重构目标。不引入 LiteLLM（3 个 provider 不值得重量级依赖）、不引入 LangChain/LangGraph（matmaster 本身就是 agent 框架），事件总线用 asyncio.Queue 或同步 queue.Queue 自建（约 60 行代码）。唯一的开发工具增强是添加 pytest-asyncio 和 mypy 到 dev 依赖。

最大的风险集中在 brownfield 迁移的具体操作上：事件排序在从 callback 迁移到 MessageBus 时可能丢失（前端 SSE 依赖严格的事件序）；兼容适配层如果没有明确的 sunset deadline 会成为永久的翻译税；agent kernel 如果只提取 loop 骨架而不设计 hook point API，MatMasterAgent._step() 中的实际业务逻辑（guard 评估、callback pipeline、context compaction、auto-save）会留在原地无法迁移；ToolGuard 的 6 个关注点拆分为 kernel guard 和 business guard 时，共享状态（auth failure 计数影响 loop detection）的同步是隐藏的地雷。这些风险都有明确的阶段性防范策略，关键是在设计阶段（而非实现阶段）解决接口问题。

## Key Findings

### Recommended Stack

不引入新的核心依赖。现有 pyproject.toml 中的依赖已覆盖全部重构需求，重构的本质是用现有工具重新组织架构边界。

**Core technologies:**
- **Pydantic v2 (>=2.12, pin <3):** 层间契约定义 -- frozen model 提供不可变性 + 运行时验证 + JSON Schema 导出，discriminated union 做事件类型区分
- **typing.Protocol (stdlib):** 组件接口定义 -- 结构化子类型不强制继承，允许旧类直接满足新接口而无需修改继承链
- **原生 LLM SDK (openai/anthropic/google-genai):** LLM Provider 实现 -- 对 reasoning_protocol/temperature_policy/thinking blocks 的精细控制是 LiteLLM 无法覆盖的
- **asyncio.Queue / queue.Queue (stdlib):** 事件总线传输 -- 进程内事件路由，零外部依赖，跨 worker 沿用现有 Redis pub/sub
- **mcp (>=1.0, pin <2):** MCP 协议集成 -- v2 处于 pre-alpha，必须 pin 到 1.x 避免 breaking changes

**Critical version note:** MCP SDK 当前未设上限约束，需立即 pin 到 `<2`。

### Expected Features

**Must have (table stakes -- v1):**
- AgentRuntimeSpec / PlaygroundContext 类型化契约 -- 整个重构的锚点，替代 Dict[str, Any]
- 纯化 Agent Loop -- 只消费 AgentRuntimeSpec，剥离 config 装配
- LLM Provider 抽象接口 -- chat() + chat_with_retry() + streaming 统一协议
- Tool Registry 统一 -- 内置工具和 MCP 工具统一注册路径
- Context Builder -- 从 identity/skills/memory/task 多源组装 system prompt
- Context Compaction 迁移 -- 已有成熟实现（sliding_window/summary/latest_half），收入 AgentRuntimeSpec
- 通用 Guard (loop detection) -- kernel 内置安全机制，不可移除
- Termination Policy -- max_turns / finish tool / stop_event 统一抽象
- mat_master 完整迁移 -- 在新骨架上跑通 mat_master 全流程

**Should have (differentiators -- v1.x):**
- MessageBus 事件系统 -- 替代 callback 直连，实现 agent 与消费方解耦
- Guard 分层注入 -- 业务 guard 通过 AgentRuntimeSpec.guards 注入
- Solver 收入 exp 层 -- 作为 exp 组合 agent 的高阶模式（注意 ResearchPlanner 是多 agent orchestrator）
- 兼容适配层 (CompatAdapter) -- Strangler Fig 模式桥接旧 playground
- 单元测试覆盖 -- 三层契约 + kernel hook points

**Defer (v2+):**
- Multi-Agent 编排层 -- 先完成单 agent 解耦
- 跨 Session 记忆服务 -- 不是 kernel 职责，通过 ContextBuilder 外部注入
- Observability/Tracing 集成 -- 通过 MessageBus 接入，需要 bus 先稳定
- Handoff 协议 (Agent-to-Agent)
- 内置 Memory/Knowledge Store -- kernel 只管 in-context messages

**Anti-features (明确不做):**
- 前端协议内置 (ag-ui/SSE) -- agent 不应知道传输协议
- 配置热更新 -- frozen spec + 重新创建实例
- Tool 执行沙箱 -- Session 类型决定隔离级别
- 多租户隔离 -- 属于 Service 层

### Architecture Approach

三层职责清晰分离：Playground 层负责环境准备（session/workdir/MCP/config），输出 PlaygroundContext；Exp 层负责能力装配（LLM/tools/guards/prompts），消费 PlaygroundContext 输出 AgentRuntimeSpec；Agent Kernel 负责纯执行循环（LLM call -> tool exec -> msg accumulate -> loop），只消费 AgentRuntimeSpec + TaskInstance。层间通过 Pydantic frozen model 通信，不通过 dict 或继承。事件通过 EventBus 解耦 agent 和消费方（SSE/persistence/logging）。

**Major components:**
1. **PlaygroundContext (Pydantic BaseModel)** -- Playground 层唯一输出，包含 session/workdir/config/mcp_manager/skill_registry/env_vars
2. **AgentRuntimeSpec (Pydantic BaseModel)** -- Exp 层唯一输出，包含 llm/tools/context_builder/guards/termination/compaction/hooks
3. **AgentKernel** -- 纯执行循环，通过 hook points（pre_tool_call/post_tool_call/pre_llm_call/should_continue）扩展，不通过继承
4. **EventBus + QueueBridge** -- 同步事件总线（适配当前 ThreadPoolExecutor 线程模型），QueueBridge 桥接到 SSE 消费
5. **GuardPipeline** -- 串联执行多个 Guard，kernel 内置 loop detection + max turns，业务 guard 由 Exp 注入
6. **CompatAdapter** -- Strangler Fig 模式桥接旧 playground 到新契约，带明确 sunset deadline

**Key architectural decisions:**
- 同步 EventBus 而非 asyncio.Queue：因为 Agent 在 ThreadPoolExecutor 中同步运行，不在 asyncio event loop 中
- 构造函数注入而非 DI 容器：项目复杂度不需要 python-dependency-injector 级别的 DI
- Protocol 而非 ABC：不强制继承，旧类可直接满足新接口
- Compaction 拆分为 policy (kernel) 和 implementation (exp-injected)：kernel 定义 ContextPolicy 接口，exp 注入具体实现

### Critical Pitfalls

1. **Event Ordering Loss (callback -> MessageBus)** -- 前端 SSE 依赖严格的事件序（thought start->streaming->end -> tool_call -> tool_result）。防范：使用 single-producer-per-agent-run channel 保持 FIFO，迁移前先录制当前事件序列作为 integration test baseline
2. **Adapter Becomes Permanent** -- CompatAdapter 会因为"能用"而永远留下。防范：设 concrete sunset milestone，adapter 从 day one 加 DeprecationWarning，新功能只走新契约
3. **Typed Contract Explosion** -- 167 个 Dict[str, Any] 不是 167 个 Pydantic model。防范：只在三个边界（PlaygroundContext/AgentRuntimeSpec/EventBus payload）创建 Pydantic model，内部用 dataclass/TypedDict
4. **Kernel Too Thin (no hook points)** -- 如果只提取 loop 骨架，MatMasterAgent._step() 中的 guard/callback/compaction/auto-save 逻辑无法迁移。防范：先分析 _step() 需求再设计 kernel hook point API（pre_tool_call/post_tool_call/pre_llm_call/should_continue），validation 标准是 mat_master 和 minimal 都不需要 override _step()
5. **Guard State Coupling** -- ToolGuard 的 6 个关注点共享状态（auth failure 计数影响 loop detection）。防范：用 GuardChain + 共享 GuardContext 模式，而非简单拆分为独立对象

## Implications for Roadmap

### Phase 1: Foundation Contracts + EventBus
**Rationale:** PlaygroundContext 和 AgentRuntimeSpec 是所有后续阶段的基础。事件类型定义同属无外部依赖的基础层，可一起完成。如果契约不稳定，后续所有组件都会返工。
**Delivers:** 类型化契约定义（Pydantic frozen models）、Guard Protocol、TerminationPolicy、EventBus + AgentEvent 类型、QueueBridge
**Addresses:** AgentRuntimeSpec 契约 (P1)、PlaygroundContext 契约 (P1)、Termination Policy (P1)
**Avoids:** Typed Contract Explosion -- 只建边界契约，不 model 内部数据结构。建议不超过 8 个 Pydantic BaseModel

### Phase 2: Agent Kernel
**Rationale:** Kernel 依赖 Phase 1 的契约，但可独立于 Playground/Exp 单元测试（mock spec）。这是整个重构的技术核心，必须在能力装配层之前验证。
**Delivers:** AgentKernel（消费 AgentRuntimeSpec 的纯执行循环）、内置 guard（loop detection/max turns）、GuardPipeline、ContextBuilder、ContextPolicy 接口
**Addresses:** 纯化 Agent Loop (P1)、通用 Guard (P1)、Context Builder (P1)、Context Compaction 迁移 (P1)
**Avoids:** Kernel Too Thin -- 必须先从 MatMasterAgent._step() 分析出完整的 hook point 集合（pre_tool_call/post_tool_call/pre_llm_call/should_continue），再构建 kernel。验证标准：mat_master 场景不需要 override _step()

### Phase 3: Exp Assembly Layer + LLM Provider
**Rationale:** Exp 层消费 PlaygroundContext、输出 AgentRuntimeSpec。LLM Provider 抽象是 Exp 组装 spec 的前置依赖（需要 create_llm() 工厂）。Phase 3 和 Phase 4 可以并行（Exp 和 Playground 只通过 PlaygroundContext 通信）。
**Delivers:** Base Exp class + assemble() 方法、MatMasterExp（guard injection/solver routing）、MinimalExp、LLMProvider Protocol + OpenAI/Anthropic/Google 实现、ToolRegistry 统一（builtin + MCP + skill）
**Addresses:** LLM Provider 抽象 (P1)、Tool Registry 统一 (P1)、Guard 分层注入 (P2)、Solver 收入 exp 层 (P2)
**Avoids:** Guard State Coupling -- 使用 GuardChain + GuardContext 共享状态；Solver Absorption -- 承认 ResearchPlanner 是 multi-agent orchestrator，设计 CompositeExp 或 OrchestratedExp 而非强行塞入单 agent exp

### Phase 4: Playground Layer Refactoring
**Rationale:** 与 Phase 3 可并行。Playground 只产出 PlaygroundContext，不穿透到 agent 层。
**Delivers:** 新 Playground base class（只输出 PlaygroundContext）、MatMasterPlayground 重构（session + workdir + MCP + config）、MinimalPlayground 重构
**Addresses:** PlaygroundContext 完整实现、Session/环境隔离
**Avoids:** Layer Bypass -- Playground 不直接注册 tools 或配置 guards

### Phase 5: Integration + Migration
**Rationale:** 依赖 Phase 2-4 全部就绪。集成点需要同时验证新三层管线和旧路径的兼容性。这是风险最集中的阶段。
**Delivers:** CompatAdapter（Strangler Fig 模式）、agent_run_service 集成（QueueBridge + pipeline routing）、mat_master 端到端测试、minimal 端到端测试
**Addresses:** mat_master 完整迁移 (P1)、minimal 迁移 (P2)、兼容适配层 (P2)、MessageBus 事件系统集成 (P2)
**Avoids:** Event Ordering Loss -- 录制旧事件序列作为 baseline，新 bus 必须复现；Adapter Permanence -- 从 day one 加 DeprecationWarning，设 sunset milestone

### Phase 6: Cleanup + Testing
**Rationale:** 只在新路径稳定后进行。移除旧继承链、StreamingMatMasterAgent、旧 BaseExp。
**Delivers:** 旧代码清理、单元测试覆盖、迁移文档
**Addresses:** 单元测试 (P2)、代码清理
**Avoids:** Adapter Becomes Permanent -- 此阶段是 adapter 的 sunset deadline

### Phase Ordering Rationale

- **Phase 1 先行是因为契约是所有组件的类型基础。** 如果 PlaygroundContext 或 AgentRuntimeSpec 的字段在后续阶段返工，所有消费方都要改。
- **Phase 2 在 Phase 3/4 之前是因为 Kernel 可以用 mock spec 独立测试。** 而 Exp 和 Playground 必须等 Kernel 验证 hook point API 后才能确认装配逻辑。
- **Phase 3 和 4 可以并行是因为它们只通过 PlaygroundContext 通信。** Exp 消费 PlaygroundContext 但不关心 Playground 内部实现。
- **Phase 5 在 3+4 之后是因为集成需要所有层就绪。** 过早集成会导致接口不稳定时的反复调试。
- **Phase 6 最后是因为清理必须等稳定。** 过早移除旧代码会丢失 fallback 路径。

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 2 (Agent Kernel):** 需要 research-phase 深入分析 MatMasterAgent._step() 的完整 hook point 需求。当前分析识别了 4 个 hook point，但实际迁移时可能发现更多（如 RecursionError 处理、_cancelled_from_step 逻辑、trajectory 初始化）。
- **Phase 3 (Exp Assembly):** ResearchPlanner/Solver 的吸收需要 research-phase。Planner 的多阶段生命周期（planning -> execution -> evaluation -> replan）和 turn budget 管理无法简单塞入单 agent exp 模式。
- **Phase 5 (Integration):** 事件排序保证需要 research-phase 分析当前 SSE 消费方对事件序的全部依赖（stream_state/stream_id/token_count 等 extra kwargs）。

Phases with standard patterns (skip research-phase):
- **Phase 1 (Foundation):** Pydantic frozen model + Protocol + dataclass 事件类型是标准 Python 模式，文档充分。
- **Phase 4 (Playground):** Playground 只做环境准备 + 输出 PlaygroundContext，是直接的职责收拢，无复杂设计决策。
- **Phase 6 (Cleanup):** 纯删除和测试工作。

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | 零新依赖策略基于 pyproject.toml 直接验证 + nanobot 参考验证。所有推荐技术都是现有依赖或 stdlib |
| Features | HIGH | 特性列表基于对 6 个主流 agent 框架的系统对比 + matmaster 现有能力审计。Table stakes 和 differentiators 的边界清晰 |
| Architecture | HIGH | 三层设计来自对 matmaster 当前代码 + nanobot kernel 的直接分析。契约定义有代码级别的原型。同步 EventBus 的选择基于对现有线程模型的准确判断 |
| Pitfalls | HIGH | 8 个 pitfall 全部来自对 matmaster 代码的具体分析（行号级别引用），非泛泛而谈。每个 pitfall 都有 warning signs 和 recovery strategy |

**Overall confidence:** HIGH

### Gaps to Address

- **同步 vs 异步 EventBus 的长期路径：** 当前推荐同步 EventBus 适配 ThreadPoolExecutor 线程模型，但如果未来 agent 执行切换到 asyncio，需要设计 async API 的向后兼容策略。这个决策可以推迟到 Phase 2 kernel 设计时
- **ToolExecutionContext 的字段范围：** Pitfalls 研究指出 MatToolCallbacks 有 13 处 `self.agent.` 引用需要通过 ToolExecutionContext 替代，但 context 的精确字段集合需要在 Phase 2/3 实现时逐个迁移确定
- **CompactionConfig 的所有权边界：** Compaction 同时是 kernel concern（何时触发）和 exp concern（用哪个 LLM、什么策略）。ContextPolicy 接口的精确签名需要在 Phase 2 kernel 设计时与 Phase 3 exp 设计协调
- **PlaygroundContext 中的 Any 类型字段：** session/mcp_manager/skill_registry 暂用 Any 避免循环导入，未来需要为它们定义 Protocol 接口做更严格的约束
- **MCP SDK v2 的 breaking changes 范围：** 已知 v2 在 pre-alpha，但具体哪些 API 会变尚不确认。当前 pin `<2` 是权宜之计

## Sources

### Primary (HIGH confidence)
- matmaster 代码库直接分析：evomaster/agent/agent.py, playground/mat_master/core/agent.py, stream_agent.py, tool_guard.py, callback/base.py, evomaster/core/playground.py, evomaster/core/exp.py, evomaster/agent/tools/base.py, pyproject.toml
- nanobot kernel 参考架构：agent/loop.py, bus/queue.py, providers/base.py, agent/tools/registry.py, agent/context.py
- Pydantic v2 官方文档：frozen models, discriminated unions, migration guide
- Python typing.Protocol 规范
- OpenAI Agents SDK 文档：core primitives, guardrails
- Microsoft Agent Framework 文档：compaction strategies, agent memory, agent session
- Anthropic: Effective Context Engineering for AI Agents

### Secondary (MEDIUM confidence)
- nanobot Roadmap: From Lightweight Agent to Agent Kernel (GitHub discussion)
- Langfuse: Comparing Open-Source AI Agent Frameworks (2025-03)
- Confluent: Event-Driven Multi-Agent Systems patterns
- AWS Prescriptive Guidance: Strangler Fig Pattern
- Cosmic Python: Events and the Message Bus (event ordering, handler failure)
- Shopify Engineering: Strangler Fig Pattern for legacy refactoring

### Tertiary (LOW confidence)
- LiteLLM v1.82.4 dependency analysis (web search)
- bubus v1.6.0 evaluation (web search)
- MCP SDK v2 timeline estimate (web search, unconfirmed)

---
*Research completed: 2026-03-21*
*Ready for roadmap: yes*
