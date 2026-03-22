# MatMaster 框架重构 (v2)

## What This Is

MatMaster 是基于 EvoMaster 二次开发的 AI Agent 框架，提供 playground→exp→agent 三层抽象用于运行科研场景的 agent 实验。本次重构目标是在保留三层核心抽象的前提下，重建一套职责清晰、轻量可组合的框架骨架，参考 nanobot kernel 的设计理念将执行循环、上下文准备、能力注册、模型调用拆分为稳定边界。

## Core Value

三层抽象（playground→exp→agent）必须具有清晰、稳定、可测试的职责边界：playground 只搭环境，exp 只装配能力，agent 只执行循环。

## Requirements

### Validated

<!-- 已有代码中已实现并稳定运行的能力 -->

- ✓ Agent 可执行 LLM → tool 调用循环 — existing
- ✓ 支持流式事件回调（thought/tool_call/tool_result/finish） — existing
- ✓ 多 playground 类型支持（mat_master, minimal, x_master） — existing
- ✓ 配置驱动的 agent 初始化（YAML config） — existing
- ✓ 内置工具系统（bash, editor, finish） — existing
- ✓ Skills 注册与加载 — existing
- ✓ MCP 客户端集成 — existing
- ✓ Session 管理（Docker, Local, SSH） — existing
- ✓ Context compaction（长对话压缩） — existing
- ✓ Confirmation 交互（agent 暂停/恢复） — existing
- ✓ ToolGuard（循环检测、auth failure 门控、manuscript 完成门控） — existing
- ✓ Research planner solver 模式 — existing
- ✓ FastAPI Web Service 层（API/Service/DAO） — existing
- ✓ 多 LLM provider 支持（OpenAI, Anthropic, Google） — existing
- ✓ Redis 跨 worker 协调 — existing

### Active

<!-- 本次重构需要交付的能力 -->

- [ ] 重定义 Playground 层为纯 workspace 搭建层，输出 PlaygroundContext 类型化契约
- [ ] 重定义 Exp 层为能力装配层，输出 AgentRuntimeSpec 类型化契约
- [ ] 重定义 Agent 层为轻量执行 kernel，只消费 AgentRuntimeSpec
- [ ] PlaygroundContext 只包含物理环境信息：workdir、session type、cache area、环境变量、运行元信息、workspace 归档配置（WorkspaceArchivalConfig）。不包含 mcp_manager 和 skill_registry（由 Exp 层管理）
- [ ] AgentRuntimeSpec 包含 prompt 配置、tool registry、LLM provider、termination policy、hooks、业务 guard
- [ ] 实现 MessageBus 事件系统，agent 发射事件到 bus，调用方从 bus 消费
- [ ] 实现 ToolRegistry（注册制 + JSON Schema 自描述），参考 nanobot 的 Tool 抽象
- [ ] 实现 LLMProvider 抽象接口（chat + chat_with_retry），解耦具体 provider 实现
- [ ] 实现 ContextBuilder，从多源组装 system prompt（identity + skills + memory + task）
- [ ] Guard 系统拆分：通用 guard（loop检测）内置于 agent，业务 guard（manuscript）由 exp 注入
- [ ] Solver 模式收入 exp 层，作为 exp 组合 agent 的高阶装配模式
- [ ] mat_master 完整迁移到新骨架
- [ ] minimal 迁移到新骨架
- [ ] 兼容适配层（compatibility adapter）桥接旧入口到新契约
- [ ] 新三层契约均有对应的单元测试

### Out of Scope

<!-- 本期不处理 -->

- 多 agent 编排能力 — 先完成单 agent 框架解耦，编排后续设计
- src/ Web Service 层重构 — 保持现状，不在本次范围
- x_master playground 迁移 — 优先 mat_master 和 minimal
- 完整保留所有历史隐式行为 — 采用关键入口兼容 + 适配层迁移策略
- 前端 UI 改动 — 本次只涉及后端框架层
- 配置热更新 — 属于增量功能，非解耦必需
- 多租户隔离 — 属于运维能力，非架构解耦目标

## Context

### 现状问题

当前 matmaster 经过多轮演进后存在以下核心问题：

1. **层间穿透调用**：playground 直接注册 skills，agent 的 run 方法里既有 loop 逻辑又有 config 装配，exp (BaseExp) 承担了 agent 执行和结果收集但跟装配职责不完全对齐
2. **能力散落**：prompt、tool、MCP、skill、LLM 封装等能力散落在 evomaster/core、evomaster/agent、playground/mat_master 多个位置，缺少统一装配入口
3. **Agent kernel 过重**：MatMasterAgent.run() 承担了过多非执行职责（config cascading、compaction config 组装、tool guard 初始化），导致 loop 核心不纯
4. **EvoMaster 遗留**：大量 v0.0.1 时代的设计模式（BasePlayground 的 AgentSlots、BaseExp 的 trajectory 收集）不适合现代 agent framework 需求
5. **类型安全退化**：Dict[str, Any] 大量用于配置和组件间通信，运行时才暴露错误

### 参考架构

nanobot kernel (`/Users/kealdoom/Desktop/github/nanobot/nanobot`) 的关键设计：

- **AgentLoop**：极简执行循环，只做 LLM调用 → tool执行 → 消息累积 → 循环
- **ToolRegistry**：注册制，Tool 通过 JSON Schema 自描述 + async execute
- **LLMProvider**：抽象接口 chat() + chat_with_retry()，LiteLLMProvider 等具体实现
- **ContextBuilder**：从 identity + bootstrap + memory + skills 多源组装 system prompt
- **MessageBus**：async queue 解耦 channel 与 agent core
- **Session/Memory**：追加式 JSONL 存储 + LLM 驱动的 memory consolidation

### 技术栈

- Python 3.13（最低 3.10+）
- FastAPI + Uvicorn（Web Service 层，不重构）
- 多 LLM provider（OpenAI, Anthropic, Google）
- MCP 协议集成
- Redis（跨 worker 协调）
- MySQL + PyMySQL（持久化）
- Pydantic（类型验证）

### 方案选型

**方案 B**：抽出新 kernel 与新三层契约，通过适配层兼容旧能力。

选择原因：
- 保留 playground/exp/agent 用户已熟悉的心智模型
- 用新契约重建清晰结构，而不是在旧耦合上打补丁
- 通过 compatibility adapter 控制迁移成本
- 风险低于全量重写，长期收益高于局部修补

## Constraints

- **兼容边界**: 提供关键入口兼容 + 适配层迁移，不追求完全无损兼容
- **重构范围**: 只重构 evomaster/ 和 playground/ 下的核心三层，src/ Web Service 层不动
- **迁移顺序**: mat_master 和 minimal 必须在新骨架上跑通，x_master 后续处理
- **测试要求**: 新三层契约必须有单元测试覆盖
- **技术栈**: 保持 Python，保持现有依赖栈不做大变更

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| 采用方案 B（新 kernel + 适配层） | 平衡重构收益与迁移成本，保留心智模型 | — Pending |
| PlaygroundContext 只含物理环境信息 | workspace/session/logging 是环境准备，MCP/skill 是能力装配 | Phase 4 Context |
| PlaygroundContext 不含 mcp_manager/skill_registry | MCP 和 skill 是 Exp 层职责，不通过 PlaygroundContext 传递 | Phase 4 Context |
| AgentRuntimeSpec 不包含环境信息 | agent kernel 只关心执行所需的能力配置 | — Pending |
| Guard 拆分（通用 vs 业务） | 通用 guard 是 kernel 安全机制，业务 guard 是可配置策略 | — Pending |
| Solver 收入 exp 层 | solver 是用 exp 组合 agent 的高阶模式，不是独立抽象 | — Pending |
| 事件系统用 MessageBus 解耦 | 参考 nanobot，agent 不直接持有 callback reference | — Pending |
| 参考 nanobot kernel 设计 | AgentLoop + ToolRegistry + LLMProvider 的职责划分清晰可复用 | — Pending |
| Playground = Workspace 等价 | 项目实际 1:1:1（session:playground:workspace），简化生命周期 | Phase 4 Context |
| Service 层统一读 config 并分发 | 物理环境 config 给 Playground，能力 config 给 Exp，避免 config 重复读取 | Phase 4 Context |
| 统一 Playground 类（无子类） | 重构后 mat_master/minimal 差异纯 config 驱动，无需子类 | Phase 4 Context |
| prepare/cleanup 两段式生命周期 | 1:1:1 关系下不需要三段式 setup/prepare/cleanup | Phase 4 Context |
| Exp.assemble() 中初始化 MCP/Skill | Exp 构造函数接收 config 数据，assemble 时用 ctx.workdir 初始化 MCP | Phase 4 Context |
| 混合资源释放模式 | Exp 自管能力资源（try/finally），Playground 由 Service 层编排 cleanup | Phase 4 Context |

---
*Last updated: 2026-03-22 after Phase 4 context discussion*
