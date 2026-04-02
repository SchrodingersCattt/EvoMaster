# Milestones

## v2.1 — matmaster/ 完全独立化

**Shipped:** 2026-04-02
**Phases:** 7 (25-31) | **Plans:** 19 | **Tests:** 1,294
**Status:** complete (19/19 requirements satisfied, tech debt closed by Phase 31)

### Delivered

让 `matmaster/` 运行时路径完全独立于 `evomaster/`、`playground/` 和 `src/`。三方向解耦完成后物理删除了 evomaster/ (113 files, 26k lines) 和 playground/evaluation/ (78K+ lines)，通过 AST import audit 和隔离测试证明零残留运行时依赖。

### Key Accomplishments

1. Session & Playground 原生化：matmaster 自有 Session Protocol (Local + SSH)、参数化 Playground 构造器、直接 YAML 配置加载，零 evomaster 运行时依赖
2. Tool 全面内化：bash_safety/editor helper 内联、MonitorJobTool 迁入 BuiltinTool 体系、EvoToolAdapter 删除、web_search 解耦 playground，全部工具原生注册
3. MCP/Calculation 原生链路：MCPConnection ABC + 三种传输 + MCPToolManager + 4 个 calculation 模块搬入 matmaster，LazyMCPTool 直连重写
4. src 反向依赖消除 + Consumer 迁移：BohriumSetupService 回调注入、bohrium_env 纯模块化、chat_history/agent_run_bohrium 切换 matmaster 原生类型
5. 遗留代码物理删除：evomaster/ + playground/ + evaluation/ 删除，24 个 skill 归档，配置路径统一
6. 独立性证明：AST import audit + 隔离测试脚本 + v2.1 迁移文档，1,294 tests passed

### Stats

- Source: 15,839 LOC (matmaster/)
- Timeline: 2 days (2026-04-01 → 2026-04-02)
- Commits: 178 milestone-related
- Files modified: 703 (+23,012 / -104,704 lines)
- Requirements: 19/19 satisfied
- Archive: milestones/v2.1-ROADMAP.md, milestones/v2.1-REQUIREMENTS.md

---

## v1 — MatMaster Framework Refactoring

**Shipped:** 2026-03-22
**Phases:** 7 | **Plans:** 21 | **Tests:** 380
**Status:** tech_debt (all requirements satisfied, service layer integration debt accepted)

### Delivered

将 matmaster 的 playground/exp/agent 三层架构从继承驱动重构为契约驱动。建立了类型化契约系统（PlaygroundContext/AgentRuntimeSpec/AgentEvent），实现了纯执行 AgentKernel，统一了 ToolRegistry 三源注册和 ContextBuilder 多源 prompt 组装，完成了 mat_master/minimal 端到端迁移验证。

### Key Accomplishments

1. 建立三层契约类型系统（16 种事件类型，Pydantic frozen model + discriminated union）
2. 实现纯执行 AgentKernel（4 种终止路径、内置 Guard 管线、5 个 Hook 扩展点、流式推理）
3. 构建 Exp 装配层（ToolRegistry 三源注册、ContextBuilder 分段 prompt、WorkerRegistry Protocol）
4. Playground 收缩为纯环境准备层（统一类 + config YAML 驱动两种部署形态）
5. 端到端迁移验证（380 个测试，含上游场景：run_interrupted、跨 pod 确认、Bohrium、配额管线）
6. Service 层接线 + 目录重组（engine/assembly/bus/playground → core/tools/types）

### Known Gaps

- agent_run_service.py 缺少 mcp_manager_factory/skill_registry_factory 传入
- WorkerRegistryServiceAdapter 已创建但未注入
- run_agent_sync() 返回 None vs agent_worker 期望 tuple
- FinishEvent 未 emit 到 MessageBus

### Stats

- Source: 3,456 LOC (matmaster/) + 7,590 LOC (tests/)
- Timeline: 2 days (2026-03-21 → 2026-03-22)
- Commits: ~106 milestone-related
- Requirements: 29/29 satisfied
- Archive: milestones/v1-ROADMAP.md, milestones/v1-REQUIREMENTS.md

---
*Last updated: 2026-03-22*
