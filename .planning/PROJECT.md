# MatMaster 框架演进

## What This Is

MatMaster 是面向科研场景的 AI Agent 框架内核，围绕 `playground -> exp -> agent` 三层抽象组织运行时、工具、技能与执行环境。v2.1 完成后，`matmaster/` 已是完全独立的核心包——运行时零依赖 `evomaster`/`playground`/`src`，15,839 行 Python 代码，1,294 个测试全部通过。仓库中 `src/` 服务层作为应用层正向调用 `matmaster/`，是正确的依赖方向。

## Core Value

三层抽象（playground→exp→agent）必须具有清晰、稳定、可测试的职责边界：playground 只搭环境，exp 只装配能力，agent 只执行循环。

## Requirements

### Validated

- ✓ PlaygroundContext/AgentRuntimeSpec/AgentEvent 类型化契约 — v1 (CONT-01~05)
- ✓ MessageBus 同步事件队列 + SSEHandler 生产 SSE 路径 — v1 (EBUS-01~02)
- ✓ AgentKernel 纯执行循环 + GuardPipeline + Hook API — v1 (KERN-01~04)
- ✓ LLMProvider Protocol (chat + chat_stream, async) — v1 (LLMP-01), async in Phase 12 (PROT-01)
- ✓ Exp 装配层 (ToolRegistry + ContextBuilder + WorkerRegistry Protocol) — v1 (ASBL-01~06)
- ✓ 统一 Playground + config YAML 驱动 + WorkspaceArchivalConfig — v1 (WKSP-01~04)
- ✓ mat_master/minimal 端到端迁移 + E2E 测试 — v1 (MIGR-01~02)
- ✓ 三层契约测试 + 上游场景验证 + 配额管线 + 迁移文档 — v1 (QUAL-01~05)
- ✓ Exp config 驱动 (ExpConfig Pydantic + TOML 定义 + load_exp_config) — post-v1
- ✓ LLM config 多级解析 (LLMProfileConfig + LLMRouteConfig + llm_factory) — post-v1
- ✓ ContextCompactor 运行时上下文压缩 (summary + sliding_window fallback) — post-v1
- ✓ EventRouter SRP 拆分 (event_payloads + PersistenceHandler + SSEHandler) — post-v1
- ✓ DevShell 本地开发 REPL (DevConfig + DevRunner + CLI entry) — post-v1
- ✓ Kernel 强化 (KernelRunResult + on_guard_blocked + tool 异常捕获 + stream finish 校验) — post-v1
- ✓ BuiltinTool 体系 (base ABC + BashTool/ListDirTool/TaskTools + session 注入 + Exp 双源注册) — v1.1
- ✓ 文件操作 Tools (Read/Write/Edit/Glob/Grep + ReadTracker + Read-Before-Modify 协议) — v1.1
- ✓ Tool Description/Schema 精细化 + developer_instructions system prompt — v1.1
- ✓ SubAgent Spawn 机制 (SubAgentTool + spawn_fn 闭包 + 事件路由 + stop_event 级联) — v1.1
- ✓ v2.0 async 基础设施闭环（Protocol/LLM/Tool/Hook/Bus/Kernel/Exp 全链路 async + Confirmation Flow + parallel dispatch）— v2.0
- ✓ Session & Playground 原生化（matmaster 自有 Session Protocol + 参数化 Playground + YAML 配置直读）— v2.1 Phase 25
- ✓ Tool 全面内化（bash_safety/editor 内联 + MonitorJobTool 迁入 + EvoToolAdapter 删除 + web_search 解耦）— v2.1 Phase 26
- ✓ MCP/Calculation 原生链路（MCPConnection ABC + MCPToolManager + calculation adaptors 搬入）— v2.1 Phase 27
- ✓ src 反向依赖消除 + Consumer 迁移（BohriumSetupService 回调注入 + chat_history/agent_run_bohrium 切换 matmaster 类型）— v2.1 Phase 28
- ✓ 主执行路径切换（API/worker + 本地 Web 改走 matmaster 原生入口）— v2.1 Phase 29
- ✓ 独立性证明（AST import audit + 隔离测试 + 迁移文档 + evomaster/playground 物理删除）— v2.1 Phase 30
- ✓ 技术债务收口（32 个测试修复 + 隔离脚本更新 + 文档同步）— v2.1 Phase 31
- ✓ Kernel Generator-First + Tool Runtime v2 核心骨架（_run_items generator / run_stream / ToolRunner Protocol / ToolCatalog / 8 frozen types + ToolResult payload+meta 升级 / AgentRuntimeSpec 扩展）— v2.2 Phase 32
- ✓ FullToolRunner 七步执行链 + ToolScheduler（exclusive/shared_read/counted）+ StructuralValidation + CapabilityPolicy — v2.2 Phase 33
- ✓ _run_items() yield ToolCallEvent/ToolResultEvent（KGEN-06 提前实现）— v2.2 Phase 33
- ✓ Exp.run_stream() + AgentRunService.run_agent_stream() 接入 generator 事件链 — v2.2 Phase 34
- ✓ _do_stream_llm() → _stream_llm_items() 子 generator 改造 — v2.2 Phase 34
- ✓ Hook → Bus 间接路径退役（assistant_state/skill_hit/output_processor/tool_result_hook 全部退役）— v2.2 Phase 34
- ✓ FullToolRunner 激活为默认执行路径 + run_stream() 输出 BusEvent + ToolCatalog version 消费 — v2.2 Phase 34
- ✓ ToolRegistry 降级为纯存储层 + ToolCatalog 成为唯一上层消费接口 + ReadBeforeModifyGuard/CapabilityPolicy 迁入三层约束 — v2.2 Phase 35
- ✓ 去总线化完成（MessageBus/EventRouter/ConfirmationHook 物理删除，RunEventFanout 替代，bus.py 删除）— v2.2 Phase 36
- ✓ 调度边界固化（stateless SessionCapabilities 行为回归锁定，persistent shell 并发明确延后）— v2.2 Phase 36

### Active

（v2.2 里程碑内全部需求已完成，无 Active 需求）

## Current Milestone: v2.2 AgentKernel Generator-First + Tool Runtime v2

**Goal:** 将 AgentKernel 改造为 generator-first 架构，同步建立 Tool Runtime v2 核心骨架，贯穿 Kernel → Exp → Service 全链路，最终移除 Hook→Bus 间接路径并评估去总线化

**Target features:**
- Phase 1: Kernel generator 改造 + Tool Runtime v2 核心骨架（ToolSpec/ToolBinding/ToolCatalog/ToolRunner/ToolScheduler + 三层约束）
- Phase 2: Exp/Service 接入 + 约束迁移 + Hook 退役 + ToolRegistry 降级
- Phase 3: 去总线化评估与实施 + 调度边界固化（ASCH-01 defer）

## Shipped: v2.1 matmaster/ 完全独立化 (2026-04-02)

matmaster/ 运行时路径完全独立于 evomaster/playground/src。三方向解耦、物理删除、独立性证明全部完成。详见 milestones/v2.1-ROADMAP.md。

## Deferred Goals

来自 v2.1 迁移文档 P1-P5 优先级，不在 v2.2 范围内：
- P1: 配置统一（config.yaml 中 ~/.evomaster-skills 路径待清理）
- P2: 历史路径清理（MILESTONES.md 补齐 v1.1/v2.0 历史）
- P3: 路径命名规范化（OSS 前缀、skill 文件系统路径中的 evomaster 残留）
- P4: 独立打包（matmaster 具备独立 pip install 方案）
- P5: 归档处置（.planning/phases/ 历史目录管理）
- ASCH-01: SessionCapabilities 自适应高级调度（含 persistent shell 并发）—— deferred until a persistent-shell runtime exists

## Context

### Current State

**As of 2026-04-03:** Phase 36 complete — v2.2 里程碑最后一个 phase。MessageBus/EventRouter/ConfirmationHook 物理删除，RunEventFanout 替代 queue-based transport 实现 SSE-first 直连 dispatch。bus.py 全部删除，Exp/ContextCompactor 的 `bus=` 参数清除，DevShell 改用 SimpleQueue-backed DevEventObserver。单一 `run_agent()` 入口替代 `run_agent()`/`run_agent_stream()` 双入口。stateless SessionCapabilities 调度边界通过显式回归测试固化，persistent shell 并发延后到 ASCH-01。

Tech stack: Python 3.13, Pydantic v2, FastAPI, OpenAI SDK, tiktoken.
Source: 15,839 LOC (matmaster/).

Architecture (current):
- `matmaster/core/` — AgentKernel, GuardPipeline, Hooks, Exp (config-driven), ContextBuilder, ContextCompactor, Playground
- `matmaster/config/` — ExpConfig, LLMConfig (profiles/routes), loader (YAML + TOML)
- `matmaster/exps/` — TOML exp 定义 (`direct.toml`, `explore.toml`)
- `matmaster/tools/` — ToolRegistry, BuiltinTool (原生注册), LazyMCP, SkillTool
- `matmaster/mcp/` — MCPConnection ABC, MCPToolManager, 三种传输 (stdio/sse/streamable_http)
- `matmaster/sessions/` — Session Protocol, LocalSession, SSHSession (原生 paramiko)
- `matmaster/types/` — PlaygroundContext, AgentRuntimeSpec, AgentEvent, CompactionConfig, KernelRunResult, Guards, LLMProvider, Messages, WorkerRegistry, ToolPlane/SessionCapabilities/RuntimeTopology (topology), ToolSpec/ToolBinding/ResourceClaim/ToolInstance (tool_spec), ToolDecision (tool_decision)
- `matmaster/providers/` — OpenAIProvider, llm_factory
- `matmaster/hooks/` — BaseHook (OutputProcessorHook, SkillHitHook, AssistantStateHook 已退役)
- `matmaster/integration/` — RunEventFanout, EventPayloads, PersistenceHandler, SSEHandler, WorkspaceHandler, BohriumSetupService, bohrium_env, workspace_resolver
- `matmaster/calculation/` — env_config, oss_io, job_service, path_adaptor
- `matmaster/devshell/` — DevConfig, DevRunner, DevStreamHook, EventLogger, REPL, CLI
- `matmaster/skills/` — registry 与 lazymcp skill roots

### Known Tech Debt

- `config.yaml` 中 `~/.evomaster-skills` 文件系统路径待 v2.2 清理（非代码依赖）
- `oss_io.py` 含 `evomaster/calculation` OSS 前缀字符串常量（非 import，仅 storage key）
- `tests/test_streaming_thought_protocol.py` 收集失败待纳入质量门禁

## Constraints

- **依赖方向**: `matmaster/` 运行时不得 import `evomaster`、`playground` 或 `src`；反向（src → matmaster）是正确的应用层→核心层方向
- **执行主路径**: API/worker 与 `playground/mat_master/service/server/` 需要保持可运行，不能因解耦中途失去主验证路径
- **Bohrium / Calculation**: 必须兼容当前 `bohr-agent-sdk` executor / storage / OSS 契约，不引入协议破坏
- **Phase 编号**: 沿用现有 `.planning/phases/` 历史，v2.1 从 Phase 25 继续，避免与已有 24 个 phase 目录冲突
- **质量门禁**: 必须建立 import audit 与独立测试，证明在不安装 `evomaster` / 不存在 `playground` / `src` 的环境中 `matmaster` 仍可导入和执行核心路径
- **技术栈**: 保持 Python 与现有依赖栈，不在本里程碑引入大型框架替换

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| v2.1 扩展为三方向完全独立化 | 用户目标是 matmaster 不依赖 evomaster/playground/src，一个里程碑收口 | ✓ Good |
| 本轮 phase 从 25 连续编号 | `.planning/phases/` 已存在 24 个目录，且当前无安全 archive target 支持 reset | ✓ Good |
| 本轮默认跳过外部 research | 这是 brownfield 内部解耦，已有代码耦合清单比外部生态调研更关键 | ✓ Good |
| 采用方案 B（新 kernel + 适配层） | 平衡重构收益与迁移成本，保留心智模型 | ✓ Good |
| PlaygroundContext 只含物理环境信息 | workspace/session/logging 是环境准备，MCP/skill 是能力装配 | ✓ Good |
| PlaygroundContext 不含 mcp_manager/skill_registry | MCP 和 skill 是 Exp 层职责 | ✓ Good |
| Guard 拆分（通用 vs 业务） | 通用 guard 是 kernel 安全机制，业务 guard 是可配置策略 | ✓ Good |
| Solver 收入 exp 层 | solver 是用 exp 组合 agent 的高阶模式 | ✓ Good |
| 事件系统用 MessageBus 解耦 | agent 不直接持有 callback reference | ✓ Good |
| 参考 nanobot kernel 设计 | AgentLoop + ToolRegistry + LLMProvider 职责划分清晰 | ✓ Good |
| Playground = Workspace 等价 | 1:1:1 关系简化生命周期 | ✓ Good |
| 统一 Playground 类（无子类） | mat_master/minimal 差异纯 config 驱动 | ✓ Good |
| prepare/cleanup 两段式生命周期 | 1:1:1 不需要三段式 | ✓ Good |
| Exp.assemble() 中初始化 MCP/Skill | assemble 时用 ctx.workdir 初始化 | ✓ Good |
| TYPE_CHECKING + lazy import 解决循环导入 | 运行时惰性加载 AgentKernel | ✓ Good |
| Retry at Protocol level (chat_with_retry) | 每个 provider 实现自己的重试逻辑 | ✓ Good |
| Guard shells 已删除，未来业务 guard 用 Hook | Phase 6 决策 | ⚠️ Revisit |
| Playground 参数化构造（5 keyword-only params）替代 config_path 构造 | 避免 EvoMasterConfig 耦合，直接传入运行参数 | ✓ Good |
| SSHSession + SSHEnv 合并为单类直持 paramiko.SSHClient | 消除 Env 中间层，简化生命周期 | ✓ Good |
| BohriumSetupService 回调注入替代 sessions_service 注入 | 4 个 callable 替代整个 service 对象，打破 src 反向依赖 | ✓ Good |
| evomaster/playground/evaluation 物理删除 | 解耦完成后不保留死代码，减轻仓库负担 | ✓ Good |
| Duck-typing (hasattr) 替代 isinstance 跨包类型检查 | 避免 import 耦合，保持运行时兼容 | ✓ Good |
| 目录重组 engine/assembly/bus/playground → core/tools/types | Phase 7 清理 | ✓ Good |
| Exp 改为 ExpConfig 驱动 + TOML 定义 | 消除 dict 配置，类型安全，声明式 exp 定义 | ✓ Good |
| LLM 配置引入 profile/route 两级解析 | 支持前端 model 选择 → 内部 profile 映射，model family 语义方法 | ✓ Good |
| EventRouter 拆分为 SRP 模块 | event_router.py 300+ 行 → 4 个单职责模块，可独立测试 | ✓ Good |
| ContextCompactor 用 summary 策略 | LLM 摘要压缩旧对话，sliding_window 兜底，保留最近 3+ turn | ✓ Good |
| DevShell 独立于 src/ 服务层 | 纯 matmaster/ 层开发验证，不依赖 FastAPI/Redis/MySQL | ✓ Good |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-03 after Phase 36 completion (v2.2 milestone final phase)*
