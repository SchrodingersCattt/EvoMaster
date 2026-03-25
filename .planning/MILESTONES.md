# Milestones

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
