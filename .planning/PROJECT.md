# MatMaster 框架演进

## What This Is

MatMaster 是面向科研场景的 AI Agent 框架内核，围绕 `playground -> exp -> agent` 三层抽象组织运行时、工具、技能与执行环境。项目最初借助 `evomaster/` 演进出了当前能力面，如今的主线目标是把 `matmaster/` 抽离为可独立运行、测试与发布的核心包，同时为仓库中遗留的服务层和本地 Web 调试路径保留清晰、可迁移的兼容边界。

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
- ✓ Exp 生命周期 async 化 (assemble/build_runtime/run/cleanup 全 async + async cleanup callback dispatch) — v2.0 Phase 18 (EXPL-01~03)
- ✓ SubAgent spawn async 链路 (async spawn_fn 复用 Exp.run() + SpawnTool native async execute()) — v2.0 Phase 18 (EXPL-04)
- ✓ v2.0 async 基础设施闭环（Bus/Router、Tool Dispatch、Confirmation Flow、emit_nowait 技术债收口）— v2.0 Phase 16-24

### Active

- [ ] `matmaster/` 运行时路径不再直接 import `evomaster`、`playground` 或 `src`
- [ ] `session / playground / config / tool / MCP` 边界提供 matmaster 原生实现
- [ ] `matmaster/ → src/` 反向依赖消除（bohrium_setup + script_env），改为依赖反转
- [ ] `matmaster/ → playground/` 依赖消除（web_search_tool），收归 matmaster 原生或 skill 机制
- [ ] `src/` 与本地 Web 主执行路径切换到 matmaster 原生入口或受控兼容层
- [ ] `calculation / Bohrium / skills / MCP` 能力在解耦后保持现有行为
- [ ] 通过解耦审计与测试门禁证明 matmaster 可脱离 evomaster / playground / src 独立运行

## Current Milestone: v2.1 matmaster/ 完全独立化

**Goal:** 让 `matmaster/` 运行时路径不再 import `evomaster/`、`playground/` 或 `src/`，成为可独立运行、测试与发布的核心包。

**Target features:**
- evomaster/ 解耦（~15 imports, 8 files）：session/config/playground 原生化、builtin tool helper 内化、MCP/calculation 链路收回、EvoToolAdapter 消除
- playground/ 解耦（1 import）：`exp.py` 中 `get_web_search_tool` 收归 matmaster 或通过 skill/tool 注册机制替代
- src/ 反向依赖消除（6 imports, 2 files）：`bohrium_setup.py` 的 5 个 service 函数和 `script_env.py` 的常量引用改为依赖反转
- 主执行路径迁移：API/worker 与本地 Web 初始化逻辑改用 matmaster 原生入口
- 质量门禁：import audit + 独立测试证明 matmaster 可脱离上述三者独立运行

### Out of Scope

- 新产品能力或前端交互改版 — 本里程碑聚焦架构解耦，不扩展用户可见功能面
- `playground/mat_master/core/` 历史 solver 体系的全面重写 — 仅处理阻塞 matmaster 独立运行的依赖点
- `bohr-agent-sdk` 服务端协议调整 — 继续兼容当前 executor / storage / OSS 契约
- `src/` 对 `matmaster/` 的正向依赖清理 — 应用层调核心层是正确方向，不在本次范围
- `playground/ → src/` 和 `playground/ → matmaster/` 的依赖 — 本里程碑只关注 matmaster 的出向依赖

## Context

### Current State

**As of 2026-04-01:** Phase 25 完成，matmaster session/playground 原生化。Phase 26 完成，matmaster.tools 完全原生化。Phase 27 完成，MCP 与 Calculation 原生链路收回 matmaster 侧：`matmaster/mcp/` 包（MCPConnection + MCPToolManager 精简版）、`matmaster/adaptors/calculation/` 包（4 模块迁移）、LazyMCPTool 直连 MCPConnection.call_tool、全部 evomaster MCP/calculation import 切换完毕。

Tech stack: Python 3.13, Pydantic v2, FastAPI, OpenAI SDK, tiktoken.

Architecture (current):
- `matmaster/core/` — AgentKernel, GuardPipeline, Hooks, Exp (config-driven), ContextBuilder, ContextCompactor, MessageBus, Playground
- `matmaster/config/` — ExpConfig, LLMConfig (profiles/routes), loader (YAML + TOML)
- `matmaster/exps/` — TOML exp 定义 (`direct.toml`, `explore.toml`)
- `matmaster/tools/` — ToolRegistry, EvoToolAdapter, BuiltinTool, LazyMCP, SkillTool
- `matmaster/types/` — PlaygroundContext, AgentRuntimeSpec, AgentEvent, CompactionConfig, KernelRunResult, Guards, LLMProvider, Messages, WorkerRegistry
- `matmaster/providers/` — OpenAIProvider, llm_factory
- `matmaster/hooks/` — ConfirmationHook, OutputProcessorHook, SkillHitHook, AssistantStateHook
- `matmaster/integration/` — EventRouter, EventPayloads, PersistenceHandler, SSEHandler, WorkspaceHandler, BohriumSetupService
- `matmaster/devshell/` — DevConfig, DevRunner, DevStreamHook, EventLogger, REPL, CLI
- `matmaster/skills/` — registry 与 lazymcp skill roots

### Decoupling Inventory (2026-04-01, revised)

`matmaster/` 的三方向出向依赖全景：

**A. matmaster/ → evomaster/（~15 imports, 8 files）**
1. **Playground / Session / Config** — `core/playground.py` 依赖 BaseSession、LocalSessionConfig、ConfigManager、PlaygroundSessionMixin 及 docker/ssh session
2. ~~**Tool / MCP / Calculation** — `tools/lazy_mcp.py`、`cache_mcp_schemas.py`、`eval_tooling_snapshot.py` 依赖 evomaster MCP manager 与 calculation adaptor~~ — Resolved in Phase 27; `core/exp.py` 的 EvoToolAdapter 仍待 Phase 28+
3. **Builtin Helper** — `tools/builtin/bash_tool.py` 与 `edit_tool.py` 复用 evomaster 安全检查和编辑辅助

**B. matmaster/ → playground/（1 import）**
4. **Web Search Tool** — `core/exp.py:396` lazy import `playground.mat_master.tools.web_search.get_web_search_tool`

**C. matmaster/ → src/（6 imports, 2 files）**
5. **Bohrium Setup 反向依赖** — `integration/bohrium_setup.py` 有 5 个 lazy import 从 `src.services.agent_run_bohrium` 拉函数（load_run_credentials、apply_run_credentials_to_session、setup_bohrium_for_run、cleanup_bohrium_after_run、BohriumSetupResult）
6. **Script Env 常量** — `tools/script_env.py:59` lazy import `src.utils.constant.BOHRIUM_OPENAPI_HOST`

### Post-v1 Changes (untracked by GSD)

v1 之后在 GSD 体系外进行的 6 个主要特性开发：

1. **Config-Driven Exp**: Exp 从 dict 参数改为 ExpConfig Pydantic model，exp 定义通过 TOML 文件 (`matmaster/exps/*.toml`) 加载。DirectExp 已删除。
2. **LLM Config System**: LLMProfileConfig (semantic methods: effective_family/temperature/build_extra_kwargs) + LLMRouteConfig 路由解析 + llm_factory.build_provider() 工厂函数。
3. **Context Compaction**: ContextCompactor (summary + sliding_window fallback)，集成到 kernel 执行循环，支持独立 compaction LLM 配置。
4. **EventRouter SRP Split**: 原 event_router.py 拆为 EventRouter + EventPayloads + PersistenceHandler + SSEHandler 四个单职责模块。
5. **DevShell**: 完整的本地开发 REPL (`matmaster/devshell/`)，支持 YAML 配置、多轮对话历史、事件日志、终端流式输出。
6. **Kernel Hardening**: KernelRunResult 返回类型、on_guard_blocked hook、tool 异常捕获、stream finish 校验、identity config 转发。

### Known Tech Debt

- `matmaster/core/playground.py` 仍是最大 runtime 耦合点，session / config / mixin 三类依赖未抽离
- `matmaster/core/exp.py` 仍挂着 `EvoToolAdapter` 与 `MonitorJobTool`，tool 注册链路并未完全原生化
- ~~`matmaster/tools/lazy_mcp.py` 与 calculation path adaptor 仍建立在 `evomaster` MCP manager/adaptor 之上~~ — Resolved in Phase 27
- `src/services/chat_history.py` 和本地 Web 初始化路径仍以 `evomaster` 类型与入口为事实标准
- `tests/test_streaming_thought_protocol.py` 仍有收集失败问题，需要在解耦期纳入统一质量门禁

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
| v2.1 扩展为三方向完全独立化 | 用户目标是 matmaster 不依赖 evomaster/playground/src，一个里程碑收口 | — Pending |
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
*Last updated: 2026-04-01 after Phase 27 completion (MCP/calculation native linkage)*
