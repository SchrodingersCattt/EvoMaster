# Feature Research

**Domain:** AI Agent Framework Kernel (科研场景 Agent 系统重构)
**Researched:** 2026-03-21
**Confidence:** HIGH

基于对 2025-2026 年主流 Agent 框架 (OpenAI Agents SDK, LangGraph, Microsoft Agent Framework/Semantic Kernel, CrewAI, nanobot) 的系统调研，以及 matmaster 现有代码和 PROJECT.md 中定义的重构目标，得出以下特性全景图。

---

## Feature Landscape

### Table Stakes (Agent Kernel 必备能力)

任何现代 agent kernel 都需要具备的基础能力。缺失任何一项都意味着框架不完整。

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **极简执行循环 (Agent Loop)** | 所有框架的核心：LLM 调用 -> tool 执行 -> 消息累积 -> 循环判断。nanobot 的 `_run_agent_loop()` 在 500 行内完成全部核心逻辑。OpenAI SDK 同样以极简 Runner 驱动 loop | MEDIUM | matmaster 现有 `BaseAgent.run()` 已实现，但混入了 config 装配逻辑。重构核心是将 loop 纯化：只消费 AgentRuntimeSpec，不做装配 |
| **Tool Registry (注册制工具系统)** | 行业标准。所有框架均使用 name -> tool 的注册表 + JSON Schema 自描述。OpenAI SDK 通过 Pydantic model 或 raw schema 定义参数；nanobot 用 `ToolRegistry` 做 name-keyed dict | LOW | matmaster 的 `ToolRegistry` 已经是成熟实现（支持 register/unregister/get_tool_specs）。重构时保持接口不变，确保 MCP 工具和内置工具统一注册即可 |
| **LLM Provider 抽象** | 多模型支持是基本要求。nanobot 定义 `LLMProvider` 抽象基类 + `chat_with_retry()` 接口；Microsoft Agent Framework 通过 Kernel 层统一 provider 调度。2025 年 LiteLLM 仍是 Python 生态最广泛的 provider 路由层 | MEDIUM | matmaster 已有 `BaseLLM` 抽象 + OpenAI/Anthropic/DeepSeek 实现。重构需要将其统一为 `LLMProvider` 协议接口，保证 `chat()` + `chat_with_retry()` 签名一致，且 streaming 是一等公民 |
| **Context Builder (多源 Prompt 组装)** | Anthropic 官方指南强调 system prompt 应结构化分区（identity/instructions/tools/output）。nanobot 的 `ContextBuilder` 从 identity + bootstrap + memory + skills 多源组装 | MEDIUM | matmaster 现有 prompt 组装散落在 Agent 和 Playground 中。需要收拢为独立的 `ContextBuilder`，输入源包括：identity prompt、skill descriptions、memory/history、task description |
| **Context Compaction (上下文压缩)** | 长运行 agent 的刚需。Microsoft Agent Framework 提供 truncation + summarization 两种策略；Anthropic 推荐 tool result clearing 作为最轻量方案；nanobot 用 MEMORY.md 做 token 超限时的 consolidation | HIGH | matmaster 已有成熟的 `CompactionConfig` + `ContextManager`，支持 sliding_window/summary/latest_half 策略。这是存量优势，重构时保持能力，将 compaction 配置收入 AgentRuntimeSpec |
| **流式事件发射 (Event Emission)** | 2025 年所有框架都支持 streaming。Google ADK 引入 streaming tools；Microsoft Agent Framework 定义了 ExecutorInvokeEvent/CompleteEvent/ErrorEvent 等事件类型。实时反馈是 Web 应用必需 | MEDIUM | matmaster 现用 callback 模式 (`event_callback(source, type, content, **extra)`)。重构计划引入 MessageBus (async queue) 解耦 agent 和消费方，与 nanobot 模式对齐 |
| **Termination Policy (终止策略)** | agent 何时停止执行。所有框架提供：max_turns 限制、finish tool 显式结束、stop_event 外部取消、异常终止。OpenAI SDK 的 Runner 支持 max_turns + tool-triggered finish | LOW | matmaster 已有 max_turns + FinishTool + stop_event。将终止策略抽象为 TerminationPolicy 类型，作为 AgentRuntimeSpec 的一部分 |
| **Session / 环境隔离** | agent 执行需要隔离的工作环境。Microsoft Agent Framework 的 `AgentSession` 是 stateless agent + stateful session 的分离设计。matmaster 的场景更重：需要 Docker/Local/SSH 三种 session 类型 | LOW | matmaster 的 `BaseSession` 体系已成熟。重构时 session 信息收入 PlaygroundContext（属于 workspace 准备），agent kernel 不直接管理 session lifecycle |
| **配置驱动初始化** | YAML/JSON 配置驱动 agent 行为是框架标配。nanobot 用 `config.json` + Pydantic schema 验证；matmaster 已有 YAML config 体系 | LOW | 保持 YAML 配置，但通过类型化契约 (PlaygroundContext / AgentRuntimeSpec) 替代 Dict[str, Any] 的隐式传递 |
| **Human-in-the-Loop (确认交互)** | agent 执行高风险操作前暂停等待人类确认。OpenAI SDK 的 Guardrails 支持 input/output 验证 + 拦截；matmaster 已有 confirmation_request/reply 机制 | LOW | 已有实现。重构时 confirmation 作为 hook 注入 agent kernel，通过 AgentRuntimeSpec 的 hooks 字段配置 |

### Differentiators (竞争优势 / 面向未来的能力)

不是必须的，但能显著提升框架质量或为未来扩展铺路的能力。

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Guard 系统分层 (通用 Guard + 业务 Guard)** | OpenAI SDK 将 Guardrails 作为四大原语之一（与 Agent/Tool/Handoff 并列），但其 guard 是通用的 input/output validation。matmaster 的独特价值在于将 guard 分为两层：kernel 内置通用 guard（loop 检测、auth failure 门控）+ exp 注入业务 guard（manuscript 完成门控、structure retrieval 门控）。这种分层在主流框架中不常见 | MEDIUM | 当前 `ToolGuard` 把 6 种 guard 混在一个类中。重构要拆分：通用 guard 内置于 agent kernel（属于安全机制），业务 guard 通过 AgentRuntimeSpec.guards 注入（属于可配置策略） |
| **类型化层间契约 (PlaygroundContext / AgentRuntimeSpec)** | 主流框架中 Microsoft Agent Framework 的 `AgentSession` 做到了 agent stateless + session stateful 的清晰分离。matmaster 更进一步：三层抽象各有类型化契约，playground 输出 PlaygroundContext，exp 输出 AgentRuntimeSpec，agent 只消费 AgentRuntimeSpec。这种契约驱动的层间通信比 Dict[str, Any] 更安全 | MEDIUM | 这是本次重构的核心差异化。需要设计 PlaygroundContext (workdir/session type/cache/env) 和 AgentRuntimeSpec (prompt config/tool registry/llm provider/termination/hooks/guards) 的 Pydantic model |
| **MessageBus 事件解耦** | 区别于 callback 直连，MessageBus (async queue) 实现 agent 和消费方的完全解耦。nanobot 用 `asyncio.Queue` 双通道；Confluent 提出 event bus 是多 agent 系统的基础设施。对未来多 agent 扩展是关键铺垫 | MEDIUM | matmaster 当前用 callback。重构引入 MessageBus 后，agent 发射事件到 bus，service 层从 bus 消费。为未来 agent-to-agent 通信预留接口 |
| **Solver 作为 Exp 组合模式** | 多 agent 协作的轻量级形式。不是完整的 multi-agent orchestration，而是 exp 层用多个 agent 组合解决问题的高阶模式（如 planner + executor）。CrewAI 的 crew 模式和 OpenAI SDK 的 Handoff 都属于此类 | MEDIUM | 当前 solver 模式已存在但位置不清晰。收入 exp 层后，solver 成为 exp 组合 agent 的标准方式，不需要引入独立的 orchestration 层 |
| **Skill 注册与加载系统** | skill 作为可复用的 prompt + tool 组合包，是 matmaster 科研场景的独特需求。nanobot 用 skills 目录做类似的事情，但没有注册制 | LOW | 已有 `SkillRegistry`。重构时 skill 加载收入 exp 层的能力装配流程，通过 ContextBuilder 将 skill descriptions 注入 prompt |
| **MCP 协议集成** | MCP 在 2025 年 12 月由 Anthropic 捐给 Linux Foundation，成为事实标准。OpenAI SDK 内置 MCP server 支持；Microsoft Agent Framework 讨论了 MCP 驱动的 multi-agent 模式 | LOW | matmaster 已有 MCP 客户端集成。重构时将 MCP 工具统一到 ToolRegistry，不需要特殊路径 |
| **Workspace Snapshot / 状态快照** | 在 agent 执行前后保存工作区状态，用于实验可复现性和 debug。这是科研场景的特殊需求 | LOW | 属于 playground 层的 workspace 管理能力。重构时归入 PlaygroundContext 的生命周期 |

### Anti-Features (明确不在 Kernel 中构建的能力)

这些能力看起来有价值，但在 agent kernel 中构建会破坏简洁性或引入不必要的复杂度。

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| **完整的 Multi-Agent 编排引擎** | LangGraph 的 graph-based workflow、CrewAI 的 crew 模式都很吸引人。似乎 agent framework 应该支持复杂的多 agent 协调 | 引入 graph runtime、state machine、agent discovery 等重量级抽象，与极简 kernel 设计相悖。PROJECT.md 明确标记 out of scope。先完成单 agent 解耦才有资格谈编排 | Solver 模式（exp 层组合多 agent）覆盖当前需求。未来需要时在 exp 层之上构建编排层，kernel 保持不变 |
| **Agent 内置 Memory/Knowledge Store** | nanobot 有 MEMORY.md；LangGraph 有 state persistence；似乎 agent 应该有持久化记忆 | kernel 管理 memory persistence 会引入 I/O 依赖、存储后端耦合、一致性问题。agent kernel 应该只管执行循环中的 in-context messages | Context compaction 处理 in-session 记忆。跨 session 记忆通过 ContextBuilder 从外部注入（如 memory retrieval service），不在 kernel 内部 |
| **配置热更新** | 运行时动态修改 agent 配置（切换 model、调整 max_turns 等）看起来很灵活 | 增加状态一致性复杂度。agent 正在执行循环时修改配置可能导致不可预测行为 | AgentRuntimeSpec 在创建时冻结（Pydantic frozen=True），重新创建 agent 实例来应用新配置 |
| **内置 Observability/Tracing** | OpenAI SDK 内置 tracing；LangSmith 为 LangGraph 提供 observability | 把 telemetry 耦合进 kernel 会增加依赖和复杂度。不同部署环境的 tracing 需求差异大 | 通过 MessageBus 事件流实现。消费方自行接入 tracing backend（OpenTelemetry、日志等）。kernel 只负责发射事件 |
| **前端协议内置 (ag-ui/SSE)** | 当前 event_callback 直接生成 SSE 格式事件，agent 似乎应该知道前端协议 | agent kernel 不应知道传输协议。直接耦合 SSE 格式会限制在 CLI、batch、WebSocket 等场景的复用 | agent 发射领域事件到 MessageBus。Service 层负责将领域事件转换为 ag-ui/SSE 格式。协议转换在边界层完成 |
| **多租户隔离** | 生产环境需要用户间的资源隔离 | 属于运维/部署能力，不是 agent kernel 的职责 | 通过 PlaygroundContext 提供 workspace 隔离；用户级隔离在 Service 层通过 session + auth 实现 |
| **Tool 执行沙箱** | 看起来 tool 应该在隔离环境中执行以确保安全 | tool 执行环境由 Session (Docker/Local/SSH) 提供，不是 kernel 的职责。把沙箱逻辑放进 kernel 会过度耦合 | Session 类型决定执行环境的隔离级别。kernel 只通过 Session 接口调用 tool.execute() |
| **Prompt 版本管理** | 科研场景需要 prompt 可复现性和实验追踪 | 属于实验管理层面的能力，不是执行 kernel 的职责 | Playground 层在创建 PlaygroundContext 时记录 prompt 版本；实验追踪在 exp 层或外部工具中完成 |

---

## Feature Dependencies

```
[LLM Provider 抽象]
    |
    v
[Agent Loop (执行循环)]
    |
    +--requires--> [Tool Registry]
    |                  |
    |                  +--requires--> [Tool 基类 (BaseTool + JSON Schema)]
    |                  +--requires--> [MCP 集成 (统一注册)]
    |
    +--requires--> [Context Builder]
    |                  |
    |                  +--requires--> [Skill Registry]
    |                  +--requires--> [Context Compaction]
    |
    +--requires--> [Termination Policy]
    |
    +--requires--> [通用 Guard (loop 检测)]
    |
    +--enhances--> [MessageBus 事件系统]
                       |
                       +--enhances--> [Service 层 SSE 转换]

[PlaygroundContext 契约]
    |
    +--requires--> [Session 体系 (Docker/Local/SSH)]
    +--requires--> [Workspace 管理 (workdir/cache)]

[AgentRuntimeSpec 契约]
    |
    +--requires--> [LLM Provider 抽象]
    +--requires--> [Tool Registry]
    +--requires--> [Context Builder 配置]
    +--requires--> [Termination Policy]
    +--requires--> [通用 Guard + 业务 Guard 注入]
    +--requires--> [Hooks (confirmation/event)]

[Solver 模式]
    |
    +--requires--> [Agent Loop]
    +--requires--> [AgentRuntimeSpec]
    +--组合模式--> [Exp 层装配]

[业务 Guard (manuscript/structure)]
    |
    +--requires--> [Guard 基类接口]
    +--injected via--> [AgentRuntimeSpec.guards]
```

### Dependency Notes

- **Agent Loop requires LLM Provider:** loop 的每个迭代都需要调用 LLM，provider 必须先就位
- **Agent Loop requires Tool Registry:** loop 中需要查找和执行 tool，registry 是 tool dispatch 的基础
- **Context Builder requires Skill Registry:** skill descriptions 是 system prompt 的组成部分之一
- **AgentRuntimeSpec requires 所有核心组件:** 这是 exp 层装配的产物，汇聚了 agent 执行所需的一切
- **MessageBus enhances Agent Loop:** loop 可以没有 MessageBus（直接 return），但有 MessageBus 才能实现实时事件流
- **Solver 模式 requires Agent Loop + AgentRuntimeSpec:** solver 是在 exp 层用多个 agent 组合的高阶模式，基础 agent 能力必须先完成
- **业务 Guard injected via AgentRuntimeSpec:** 业务 guard 不在 kernel 内部，通过 spec 注入，所以 guard 接口要先定义好

---

## MVP Definition

### Launch With (v1 - 核心 Kernel + 契约)

最小可用的重构产物 -- 新三层骨架能跑通 mat_master 的基本流程。

- [ ] **AgentRuntimeSpec 类型化契约** -- 替代 Dict[str, Any] 的 agent 配置传递，是整个重构的锚点
- [ ] **PlaygroundContext 类型化契约** -- playground 层输出的环境信息结构化
- [ ] **纯化 Agent Loop** -- 剥离 config 装配逻辑，只消费 AgentRuntimeSpec
- [ ] **LLM Provider 抽象接口** -- `chat()` + `chat_with_retry()` + streaming 的统一协议
- [ ] **Tool Registry (保持 + 统一 MCP)** -- 内置工具和 MCP 工具统一注册路径
- [ ] **Context Builder** -- 从 identity/skills/memory/task 多源组装 system prompt
- [ ] **通用 Guard (loop 检测)** -- kernel 内置安全机制
- [ ] **Termination Policy** -- max_turns / finish tool / stop_event 的统一抽象
- [ ] **mat_master 完整迁移** -- 在新骨架上跑通 mat_master 全流程

### Add After Validation (v1.x - 解耦完善)

核心跑通后添加的能力，提升框架完整性。

- [ ] **MessageBus 事件系统** -- 替代 callback 直连，实现 agent 与消费方解耦。触发条件：core loop 稳定后
- [ ] **Guard 分层注入** -- 业务 guard 通过 AgentRuntimeSpec 注入。触发条件：通用 guard 接口稳定后
- [ ] **Solver 收入 exp 层** -- 作为 exp 组合 agent 的高阶模式。触发条件：基础 exp 装配跑通后
- [ ] **minimal playground 迁移** -- 验证框架通用性
- [ ] **兼容适配层 (Compatibility Adapter)** -- 桥接旧入口到新契约。触发条件：新骨架稳定后
- [ ] **单元测试覆盖** -- 三层契约的测试

### Future Consideration (v2+ - 扩展能力)

在框架解耦完成、验证稳定后再考虑的能力。

- [ ] **Multi-Agent 编排层** -- 在 exp 之上构建。defer 原因：先完成单 agent 解耦
- [ ] **跨 Session 记忆服务** -- 外部 memory retrieval。defer 原因：不是 kernel 职责
- [ ] **x_master 迁移** -- 低优先级的 playground 类型
- [ ] **Observability 集成** -- 通过 MessageBus 接入 tracing。defer 原因：需要 MessageBus 先稳定
- [ ] **Handoff 协议 (Agent-to-Agent)** -- 参考 OpenAI SDK 的 handoff 模式。defer 原因：需要 multi-agent 编排层先设计

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| AgentRuntimeSpec 契约 | HIGH | MEDIUM | P1 |
| PlaygroundContext 契约 | HIGH | LOW | P1 |
| 纯化 Agent Loop | HIGH | MEDIUM | P1 |
| LLM Provider 抽象 | HIGH | MEDIUM | P1 |
| Context Builder | HIGH | MEDIUM | P1 |
| Tool Registry 统一 | HIGH | LOW | P1 |
| 通用 Guard (loop 检测) | HIGH | LOW | P1 |
| Termination Policy | MEDIUM | LOW | P1 |
| mat_master 迁移 | HIGH | HIGH | P1 |
| MessageBus 事件系统 | MEDIUM | MEDIUM | P2 |
| Guard 分层注入 | MEDIUM | MEDIUM | P2 |
| Solver 收入 exp 层 | MEDIUM | MEDIUM | P2 |
| minimal 迁移 | MEDIUM | LOW | P2 |
| 兼容适配层 | MEDIUM | MEDIUM | P2 |
| 单元测试 | HIGH | MEDIUM | P2 |
| Context Compaction 迁移 | HIGH | LOW | P1 |

**Priority key:**
- P1: v1 必须交付，框架不可用则无意义
- P2: v1.x 尽快跟进，完善框架完整性
- P3: v2+ 未来考虑

---

## Competitor Feature Analysis

| Feature | OpenAI Agents SDK | nanobot | LangGraph | Microsoft Agent Framework | matmaster (重构目标) |
|---------|-------------------|---------|-----------|---------------------------|---------------------|
| **核心原语数量** | 4 (Agent/Tool/Handoff/Guardrail) | 5 (AgentLoop/ToolRegistry/LLMProvider/ContextBuilder/MessageBus) | 3 (Graph/Node/Edge) | 3 (Agent/Thread/Kernel) | 5 (AgentLoop/ToolRegistry/LLMProvider/ContextBuilder/MessageBus) |
| **Loop 设计** | Runner 驱动，极简 | `_run_agent_loop()` < 500 行 | Graph state machine | Kernel invoke pattern | 纯化后的 Agent Loop，只消费 AgentRuntimeSpec |
| **Tool 系统** | Pydantic/JSON Schema 自动生成 + MCP | ToolRegistry name-keyed dict + MCP | Tool nodes in graph | Kernel plugins + MCP | ToolRegistry + JSON Schema + MCP 统一注册 |
| **Guard 系统** | Input/Output Guardrails (并行执行) | 无内置 guard | 无内置 guard | 无内置 guard | 分层 guard (通用 kernel 内置 + 业务 exp 注入) |
| **事件系统** | Tracing + streaming | MessageBus (asyncio.Queue) | State checkpoint callbacks | Workflow events (invoke/complete/error) | MessageBus (async queue，解耦 agent 与消费方) |
| **Context 管理** | Session 持久化 | ContextBuilder + MEMORY.md compaction | State persistence + reducers | AgentSession (serializable) | ContextBuilder + CompactionConfig (多策略) |
| **Multi-Agent** | Handoff 原语 | 无 | Graph-based workflow | Handoff + Group Chat orchestration | Solver 模式 (exp 层组合)，未来编排层 |
| **配置驱动** | Python code-first | config.json + Pydantic | Python/YAML | C#/Python/Java | YAML + Pydantic 类型化契约 |
| **层间契约** | 无显式层 | 无显式层 | Graph state schema | Kernel -> Agent -> Thread | PlaygroundContext -> AgentRuntimeSpec -> Agent Loop |

### 关键洞察

1. **OpenAI SDK 的极简主义值得学习**：4 个原语覆盖 80% 场景。matmaster 的重构也应追求类似的精简度
2. **nanobot 的 kernel 哲学最接近重构目标**：极简 loop + registry 模式 + config 驱动。matmaster 的独特价值在于三层抽象带来的关注点分离
3. **Guard 分层是 matmaster 的差异化点**：主流框架要么不内置 guard，要么像 OpenAI SDK 那样只做通用 I/O validation。matmaster 的业务 guard（manuscript/structure retrieval/auth failure）是科研场景的独特需求
4. **类型化层间契约是架构优势**：主流框架多数用 dict/state 传递。Pydantic 契约能在编辑器级别提供类型安全

---

## Sources

### Official Documentation & Guides
- [Anthropic: Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) -- context management best practices (HIGH confidence)
- [OpenAI Agents SDK Documentation](https://openai.github.io/openai-agents-python/) -- core primitives design (HIGH confidence)
- [OpenAI Agents SDK Guardrails](https://openai.github.io/openai-agents-python/guardrails/) -- guard patterns (HIGH confidence)
- [Microsoft Agent Framework Overview](https://learn.microsoft.com/en-us/agent-framework/overview/) -- enterprise agent architecture (HIGH confidence)
- [Microsoft Agent Framework: Compaction](https://learn.microsoft.com/en-us/agent-framework/agents/conversations/compaction) -- compaction strategies (HIGH confidence)
- [Microsoft Agent Framework: Agent Memory](https://learn.microsoft.com/en-us/agent-framework/user-guide/agents/agent-memory) -- session/state patterns (HIGH confidence)

### Framework Analysis
- [nanobot DeepWiki Architecture](https://deepwiki.com/HKUDS/nanobot) -- kernel design philosophy (HIGH confidence)
- [nanobot Roadmap: From Lightweight Agent to Agent Kernel](https://github.com/HKUDS/nanobot/discussions/431) -- kernel vision (MEDIUM confidence)
- [Langfuse: Comparing Open-Source AI Agent Frameworks](https://langfuse.com/blog/2025-03-19-ai-agent-comparison) -- framework comparison (MEDIUM confidence)
- [Composio: OpenAI Agents SDK vs LangGraph vs Autogen vs CrewAI](https://composio.dev/blog/openai-agents-sdk-vs-langgraph-vs-autogen-vs-crewai) -- comparison matrix (MEDIUM confidence)

### Architecture & Patterns
- [Google Developers: Architecting Context-Aware Multi-Agent Framework](https://developers.googleblog.com/architecting-efficient-context-aware-multi-agent-framework-for-production/) -- context patterns (MEDIUM confidence)
- [Confluent: Four Design Patterns for Event-Driven Multi-Agent Systems](https://www.confluent.io/blog/event-driven-multi-agent-systems/) -- event bus patterns (MEDIUM confidence)
- [JAVAPRO: Why AI Agents Need a Protocol-Flexible Event Bus](https://javapro.io/2025/11/06/why-ai-agents-need-a-protocol-flexible-event-bus/) -- event architecture (MEDIUM confidence)
- [Towards Data Science: How Agent Handoffs Work](https://towardsdatascience.com/how-agent-handoffs-work-in-multi-agent-systems/) -- handoff patterns (MEDIUM confidence)
- [Jason Liu: Context Engineering Compaction Experiments](https://jxnl.co/writing/2025/08/30/context-engineering-compaction/) -- compaction research (MEDIUM confidence)

---
*Feature research for: AI Agent Framework Kernel (matmaster refactoring)*
*Researched: 2026-03-21*
