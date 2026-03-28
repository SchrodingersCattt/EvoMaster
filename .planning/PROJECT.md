# MatMaster 框架重构 (v2)

## What This Is

MatMaster 是基于 EvoMaster 二次开发的 AI Agent 框架，提供 playground→exp→agent 三层抽象用于运行科研场景的 agent 实验。v1 完成了契约驱动的三层架构重构。v1 之后持续演进：Exp 转为 TOML config 驱动、LLM 配置引入 profile/route 多级解析、实现了 ContextCompactor 运行时上下文压缩、EventRouter 拆分为 SRP 模块、新增 DevShell 本地开发 REPL。

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
- ✓ BuiltinTool 体系 (base ABC + BashTool/ListDirTool/TaskTools + session 注入 + Exp 双源注册) — v1.1 (TOOL-04,07,09)
- ✓ 文件操作 Tools (Read/Write/Edit/Glob/Grep + ReadTracker + Read-Before-Modify 协议) — v1.1 (TOOL-01~06,08)
- ✓ Tool Description/Schema 精细化 + developer_instructions system prompt — v1.1 (PRMT-01~02)
- ✓ SubAgent Spawn 机制 (SubAgentTool + spawn_fn 闭包 + 事件路由 + stop_event 级联) — v1.1 (SUBA-01~06, PRMT-03)

- ✓ Hook 系统异步化 (5 个 Hook async 化 + ConfirmationHook asyncio.Future 重构 + _bridge_loop 桥接) — v2.0 Phase 15 (HOOK-01~03)

### Active

See REQUIREMENTS.md for full v2.0 requirement list (35 items, 15 complete after Phase 15).

## Current Milestone: v2.0 matmaster 协程改造

**Goal:** 将 matmaster 框架从同步架构全链路改造为 async/await，为多 agent 编排做准备

**Target features:**
- AgentKernel async 化 — run() → async generator，tool dispatch / LLM call / guard / hook 全部 await
- LLMProvider Protocol async 化 — chat / chat_stream 改为 async 方法，OpenAIProvider 使用 AsyncOpenAI（chat_with_retry 已在 Phase 12 移除）
- 全部 BuiltinTool async 化 — run() → async，BashTool 用 asyncio.create_subprocess_exec
- Exp 生命周期 async 化 — assemble() / build_runtime() / run() 全部 async
- Hook Protocol async 化 — 所有 hook 的 Protocol 定义改为 async（Guard 保持同步，Phase 12 决策）
- MessageBus async 兼容 — 支持 asyncio.Queue 或类似异步原语
- ContextCompactor async 化 — 内部 LLM 调用改 async
- SubAgent spawn async 化 — spawn_fn 变 async，子 agent 作为协程执行

### Out of Scope

- 多 agent 编排能力 — v2.0 只做 async 基础设施，编排层后续设计
- src/ Web Service 层重构 — 保持现状，不在本次范围
- x_master playground 迁移 — 优先 mat_master 和 minimal
- 前端 UI 改动 — 本次只涉及后端框架层
- 消除 evomaster session 依赖 — v2.0 维持现状，session-dependent tool 仍通过 BaseSession
- DevShell async 改造 — 延后，用 asyncio.run() 包装调用即可

## Context

### Current State

**Phase 16 complete** (2026-03-28): 事件传输链路全面 async。MessageBus 改为 asyncio.Queue + thread-safe emit_nowait（call_soon_threadsafe），EventRouter 改为 asyncio.Task consume loop，SSEHandler/PersistenceHandler/WorkspaceHandler 全部 async handle（to_thread 处理阻塞 I/O）。service 层通过 dedicated router loop + run_coroutine_threadsafe 桥接。1048 tests passed。

Tech stack: Python 3.13, Pydantic v2, FastAPI (not refactored), OpenAI SDK, tiktoken.

Architecture (current):
- `matmaster/core/` — AgentKernel, GuardPipeline, Hooks, Exp (config-driven), ContextBuilder, ContextCompactor, MessageBus, Playground
- `matmaster/config/` — ExpConfig, LLMConfig (profiles/routes), loader (YAML + TOML)
- `matmaster/exps/` — TOML exp 定义 (direct.toml, explore.toml)
- `matmaster/tools/` — ToolRegistry, EvoToolAdapter, BuiltinTool (base + BashTool/ListDirTool/TaskTools/SubAgentTool)
- `matmaster/types/` — PlaygroundContext, AgentRuntimeSpec, AgentEvent, CompactionConfig, KernelRunResult, Guards, LLMProvider, Messages, WorkerRegistry
- `matmaster/providers/` — OpenAIProvider, llm_factory (build_provider)
- `matmaster/hooks/` — ConfirmationHook, OutputProcessorHook, SkillHitHook, AssistantStateHook
- `matmaster/integration/` — EventRouter, EventPayloads, PersistenceHandler, SSEHandler, WorkspaceHandler, BohriumSetupService
- `matmaster/devshell/` — DevConfig, DevRunner, DevStreamHook, EventLogger, REPL, CLI
- `matmaster/skills/` — placeholder

### Post-v1 Changes (untracked by GSD)

v1 之后在 GSD 体系外进行的 6 个主要特性开发：

1. **Config-Driven Exp**: Exp 从 dict 参数改为 ExpConfig Pydantic model，exp 定义通过 TOML 文件 (`matmaster/exps/*.toml`) 加载。DirectExp 已删除。
2. **LLM Config System**: LLMProfileConfig (semantic methods: effective_family/temperature/build_extra_kwargs) + LLMRouteConfig 路由解析 + llm_factory.build_provider() 工厂函数。
3. **Context Compaction**: ContextCompactor (summary + sliding_window fallback)，集成到 kernel 执行循环，支持独立 compaction LLM 配置。
4. **EventRouter SRP Split**: 原 event_router.py 拆为 EventRouter + EventPayloads + PersistenceHandler + SSEHandler 四个单职责模块。
5. **DevShell**: 完整的本地开发 REPL (matmaster/devshell/)，支持 YAML 配置、多轮对话历史、事件日志、终端流式输出。
6. **Kernel Hardening**: KernelRunResult 返回类型、on_guard_blocked hook、tool 异常捕获、stream finish 校验、identity config 转发。

### Known Tech Debt

- Exp.build_runtime() 的 skills/mcp 参数为 stub（factory 机制待 service 层注入）
- run_agent_sync() 返回值与 agent_worker.py 不匹配（src/ 层问题）
- FinishEvent 未 emit 到 MessageBus
- 业务 Guard 注入点保留但未实际使用
- tests/test_streaming_thought_protocol.py 收集失败（1 error）

## Constraints

- 兼容边界: 提供关键入口兼容 + 适配层迁移，不追求完全无损兼容
- 重构范围: 只重构 evomaster/ 和 playground/ 下的核心三层，src/ Web Service 层不动
- 迁移顺序: mat_master 和 minimal 必须在新骨架上跑通，x_master 后续处理
- 测试要求: 新三层契约必须有单元测试覆盖
- 技术栈: 保持 Python，保持现有依赖栈不做大变更

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
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
*Last updated: 2026-03-28 after Phase 16 complete (MessageBus + EventRouter async transformation)*
