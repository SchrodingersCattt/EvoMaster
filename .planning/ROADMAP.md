# Roadmap: MatMaster Framework Evolution

## Milestones

- ✅ **v1 MatMaster Framework Refactoring** - Phases 1-7 (shipped 2026-03-22)
- ✅ **v1.1 Agent 外围能力构建** - Phases 8-11 (shipped 2026-03-25)
- ✅ **v2.0 matmaster 协程改造** - Phases 12-24 (shipped 2026-03-30)
- ✅ **v2.1 matmaster/ 完全独立化** — Phases 25-31 (shipped 2026-04-02)
- 🚧 **v2.2 AgentKernel Generator-First + Tool Runtime v2** — Phases 32-36 (in progress)

## Phases

<details>
<summary>✅ v1 MatMaster Framework Refactoring (Phases 1-7) -- SHIPPED 2026-03-22</summary>

- [x] Phase 1: Foundation Contracts (2/2 plans) - completed 2026-03-21
- [x] Phase 2: Agent Kernel (3/3 plans) - completed 2026-03-22
- [x] Phase 3: Exp Assembly Layer (4/4 plans) - completed 2026-03-22
- [x] Phase 4: Playground Layer (3/3 plans) - completed 2026-03-22
- [x] Phase 5: Integration and Quality (5/5 plans) - completed 2026-03-22
- [x] Phase 6: Service Layer Wiring (2/2 plans) - completed 2026-03-22
- [x] Phase 7: Cleanup and Traceability (2/2 plans) - completed 2026-03-22

Full details: milestones/v1-ROADMAP.md

</details>

<details>
<summary>✅ v1.1 Agent 外围能力构建 (Phases 8-11) -- SHIPPED 2026-03-25</summary>

- [x] Phase 8: BuiltinTool 基础设施与核心 Tools (3/3 plans) - completed 2026-03-24
- [x] Phase 9: 文件操作 Tools (3/3 plans) - completed 2026-03-25
- [x] Phase 10: Tool Description 与 System Prompt 设计 (2/2 plans) - completed 2026-03-25
- [x] Phase 11: SubAgent Spawn 机制 (3/3 plans) - completed 2026-03-25

</details>

<details>
<summary>✅ v2.0 matmaster 协程改造 (Phases 12-24) -- SHIPPED 2026-03-30</summary>

- [x] Phase 12: Protocol 层 + 测试基础设施 - completed 2026-03-26
- [x] Phase 13: LLM Provider 异步实现 - completed 2026-03-27
- [x] Phase 14: Tool 系统异步化 - completed 2026-03-27
- [x] Phase 15: Hook 系统异步化 - completed 2026-03-27
- [x] Phase 16: MessageBus + EventRouter 异步化 - completed 2026-03-28
- [x] Phase 17: AgentKernel 异步化 - completed 2026-03-28
- [x] Phase 18: Exp 生命周期异步化 - completed 2026-03-29
- [x] Phase 19: 服务层桥接 + 并行 Tool Dispatch - completed 2026-03-29
- [x] Phase 20: Confirmation Flow Recovery - completed 2026-03-30
- [x] Phase 21: Async Leaf I/O Cleanup - completed 2026-03-29
- [x] Phase 22: Audit Metadata Backfill - completed 2026-03-29
- [x] Phase 23: Verification + Nyquist Closure - completed 2026-03-30
- [x] Phase 24: emit_nowait Tech Debt Cleanup - completed 2026-03-29

</details>

<details>
<summary>✅ v2.1 matmaster/ 完全独立化 (Phases 25-31) — SHIPPED 2026-04-02</summary>

- [x] Phase 25: Session 与 Playground 原生化 (3/3 plans) — completed 2026-04-01
- [x] Phase 26: Tool 内化与遗留工具收归 (3/3 plans) — completed 2026-04-01
- [x] Phase 27: MCP 与 Calculation 原生链路 (3/3 plans) — completed 2026-04-01
- [x] Phase 28: src 反向依赖反转与 Consumer 迁移 (3/3 plans) — completed 2026-04-01
- [x] Phase 29: 主执行路径切换 (2/2 plans) — completed 2026-04-01
- [x] Phase 30: 解耦审计与独立性证明 (3/3 plans) — completed 2026-04-01
- [x] Phase 31: Tech Debt Cleanup (2/2 plans) — completed 2026-04-02

Full details: milestones/v2.1-ROADMAP.md

</details>

### 🚧 v2.2 AgentKernel Generator-First + Tool Runtime v2 (In Progress)

**Milestone Goal:** 将 AgentKernel 改造为 generator-first 架构，同步建立 Tool Runtime v2 核心骨架，贯穿 Kernel -> Exp -> Service 全链路，最终移除 Hook->Bus 间接路径并评估去总线化

- [ ] **Phase 32: Kernel Generator + Tool Runtime v2 核心骨架** - _run_items() generator 单一执行路径 + Tool Runtime v2 对象模型/ToolCatalog/InlineToolRunner + AgentRuntimeSpec 扩展，现有 run() 行为完全兼容
- [ ] **Phase 33: ToolRunner 完整实现 + ToolScheduler** - 完整 ToolRunner 执行链（查找/校验/调度/执行/释放）+ ToolScheduler 资源调度 + StructuralValidation/CapabilityPolicy
- [ ] **Phase 34: Exp/Service 接入 + Hook 退役** - Exp.run_stream() + AgentRunService.run_agent_stream() 接入 generator + _stream_llm_items() 子 generator + 5 个 Hook 逐步退役
- [ ] **Phase 35: 约束迁移 + ToolRegistry 降级** - read-before-modify/bash 危险命令迁入三层约束模型 + ToolBinding 字段启用 + ToolRegistry 降级为纯存储
- [ ] **Phase 36: 去总线化 + 高级调度** - MessageBus/EventRouter 消费者审计 + async fanout 替代 + Bus 移除 + SessionCapabilities 自适应调度

## Phase Details

### Phase 32: Kernel Generator + Tool Runtime v2 核心骨架
**Goal**: Kernel 拥有 generator-first 执行路径且 Tool Runtime v2 类型体系就位，所有现有调用方零修改、全量测试通过
**Depends on**: Phase 31 (v2.1 complete)
**Requirements**: KGEN-01, KGEN-02, KGEN-03, KGEN-04, KGEN-05, TOBJ-01, TOBJ-02, TOBJ-03, TOBJ-04, TOBJ-05, TOBJ-06, TOBJ-07, TOBJ-08, TCAT-01, TCAT-02, TCAT-03, TRUN-01, TRUN-02, TRUN-05, TCON-02, TRES-01, SPEC-01, TDEF-01, REGR-01, REGR-03
**Success Criteria** (what must be TRUE):
  1. `kernel.run()` 签名和返回值不变，全部现有 50+ kernel 测试零修改通过
  2. `kernel.run_stream()` 可被 `async for event in kernel.run_stream(...)` 消费，yield 出 BusEvent 序列并以 RunResultEvent 结尾
  3. ToolRunner Protocol 定义存在且 InlineToolRunner 通过 `isinstance` 检查，Kernel 通过 `spec.tool_runner` 获取 runner
  4. Tool Runtime v2 类型体系（ToolSpec/ToolBinding/ToolInstance/ResourceClaim/ToolDecision/SessionCapabilities/RuntimeTopology/ToolPlane）全部可导入且为 frozen 不可变
  5. ToolCatalog 以 base+overlay 结构运行，内部持有 ToolRegistry 作为兼容 facade，overlay 变更递增 version
**Plans:** 3 plans

Plans:
- [x] 32-01-PLAN.md — Tool Runtime v2 类型体系 + ToolResult 升级
- [ ] 32-02-PLAN.md — ToolCatalog + ToolRunner + AgentRuntimeSpec 扩展
- [ ] 32-03-PLAN.md — Kernel generator 改造 + 回归验证

### Phase 33: ToolRunner 完整实现 + ToolScheduler
**Goal**: 工具执行通过完整的 查找->校验->调度->执行->释放 链路运行，资源调度支持 exclusive/shared_read/counted 三种模式
**Depends on**: Phase 32
**Requirements**: TRUN-03, TRUN-04, TCON-01, TCON-03
**Success Criteria** (what must be TRUE):
  1. 完整 ToolRunner 执行链 ToolCatalog 查找 -> StructuralValidation -> RunStateGuard -> CapabilityPolicy -> ToolScheduler -> executor -> 释放 端到端可运行
  2. ToolScheduler 对 exclusive 资源实现互斥调度，对 shared_read 资源实现并发调度，对 counted 资源实现信号量控制
  3. StructuralValidation 对 args_schema 校验失败 / plane 未启用 / session_capabilities 不匹配的工具调用返回 deny 决策
  4. CapabilityPolicy 对 effect_level 超限或 capability 不匹配的工具调用返回 deny 决策且附带 guidance
**Plans**: TBD

### Phase 34: Exp/Service 接入 + Hook 退役
**Goal**: Generator 事件流贯穿 Kernel -> Exp -> Service 全链路，5 个 Hook 全部退役，Hook->Bus 间接事件路径移除
**Depends on**: Phase 32
**Requirements**: ESIN-01, ESIN-02, ESIN-03, HRET-01, HRET-02, HRET-03, HRET-04, HRET-05, HRET-06, REGR-02
**Success Criteria** (what must be TRUE):
  1. `Exp.run_stream()` 可消费且 cleanup 在 generator 正常结束和异常退出时均执行
  2. `AgentRunService.run_agent_stream()` 消费 generator 事件并对接 SSEHandler + PersistenceHandler，SSE 推送延迟消除 asyncio.Queue 轮询开销
  3. `_stream_llm_items()` 子 generator 逐 chunk yield LLM 流式事件，与原 EventEmitterHook 产出的事件类型和顺序一致
  4. matmaster/hooks/ 目录中 EventEmitterHook / AssistantStateHook / SkillHitHook / OutputProcessorHook 全部删除，ContextCompactor 不再依赖 Bus emit
  5. Exp.run() 和 AgentRunService.run_agent() 行为不变，现有调用方零修改
**Plans**: TBD

### Phase 35: 约束迁移 + ToolRegistry 降级
**Goal**: 工具安全检查从工具内部分散逻辑统一迁入三层约束模型，ToolRegistry 降级为纯存储后 ToolCatalog 成为唯一上层消费接口
**Depends on**: Phase 33, Phase 34
**Requirements**: CMIG-01, CMIG-02, CMIG-03, CMIG-04
**Success Criteria** (what must be TRUE):
  1. WriteTool/EditTool 中的 read-before-modify 检查代码已删除，等价逻辑由 RunStateGuard + ReadTracker 在 GuardContext 中完成
  2. BashTool 中的 `_is_dangerous_command` 检查代码已删除，等价逻辑由 CapabilityPolicy 完成
  3. ToolBinding 的 state_mode/stop_mode 字段被 Scheduler 实际消费，SessionCapabilities 变化时调度策略随之调整
  4. ToolRegistry 不再被 ContextBuilder / SkillTool / MCP 注入路径直接调用，所有上层消费通过 ToolCatalog
**Plans**: TBD

### Phase 36: 去总线化 + 高级调度
**Goal**: MessageBus/EventRouter 中间层移除（或确认保留的理由），Kernel 外事件通过 async fanout 直连消费者，ToolScheduler 支持 session 自适应
**Depends on**: Phase 34, Phase 35
**Requirements**: DBUS-01, DBUS-02, DBUS-03, ASCH-01
**Success Criteria** (what must be TRUE):
  1. MessageBus (bus.py) 和 EventRouter (event_router.py) 已删除或明确记录保留理由并附降级方案
  2. SSE 推送和持久化写入在 Bus 移除后事件数量与移除前基准一致（零事件丢失）
  3. Kernel 外事件（ErrorEvent / CancelledEvent / McpConnectEvent / BohriumNodeEvent 等）通过 async fanout 函数直连 SSEHandler + PersistenceHandler
  4. ToolScheduler 根据 SessionCapabilities 动态调整并发策略（如 persistent shell 场景下支持 shell 级并发）
**Plans**: TBD

## Progress

**Execution Order:**
Phases 32-36 execute in numeric order. Phase 35 depends on both 33 and 34 completing. Phase 36 depends on both 34 and 35 completing.

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Foundation Contracts | v1 | 2/2 | Complete | 2026-03-21 |
| 2. Agent Kernel | v1 | 3/3 | Complete | 2026-03-22 |
| 3. Exp Assembly Layer | v1 | 4/4 | Complete | 2026-03-22 |
| 4. Playground Layer | v1 | 3/3 | Complete | 2026-03-22 |
| 5. Integration and Quality | v1 | 5/5 | Complete | 2026-03-22 |
| 6. Service Layer Wiring | v1 | 2/2 | Complete | 2026-03-22 |
| 7. Cleanup and Traceability | v1 | 2/2 | Complete | 2026-03-22 |
| 8. BuiltinTool 基础设施与核心 Tools | v1.1 | 3/3 | Complete | 2026-03-24 |
| 9. 文件操作 Tools | v1.1 | 3/3 | Complete | 2026-03-25 |
| 10. Tool Description 与 System Prompt 设计 | v1.1 | 2/2 | Complete | 2026-03-25 |
| 11. SubAgent Spawn 机制 | v1.1 | 3/3 | Complete | 2026-03-25 |
| 12. Protocol 层 + 测试基础设施 | v2.0 | 2/2 | Complete | 2026-03-26 |
| 13. LLM Provider 异步实现 | v2.0 | 2/2 | Complete | 2026-03-27 |
| 14. Tool 系统异步化 | v2.0 | 2/2 | Complete | 2026-03-27 |
| 15. Hook 系统异步化 | v2.0 | 3/3 | Complete | 2026-03-27 |
| 16. MessageBus + EventRouter 异步化 | v2.0 | 2/2 | Complete | 2026-03-28 |
| 17. AgentKernel 异步化 | v2.0 | 2/2 | Complete | 2026-03-28 |
| 18. Exp 生命周期异步化 | v2.0 | 2/2 | Complete | 2026-03-29 |
| 19. 服务层桥接 + 并行 Tool Dispatch | v2.0 | 2/2 | Complete | 2026-03-29 |
| 20. Confirmation Flow Recovery | v2.0 | 2/2 | Complete | 2026-03-30 |
| 21. Async Leaf I/O Cleanup | v2.0 | 1/1 | Complete | 2026-03-29 |
| 22. Audit Metadata Backfill | v2.0 | 1/1 | Complete | 2026-03-29 |
| 23. Verification + Nyquist Closure | v2.0 | 1/1 | Complete | 2026-03-30 |
| 24. emit_nowait Tech Debt Cleanup | v2.0 | 1/1 | Complete | 2026-03-29 |
| 25. Session 与 Playground 原生化 | v2.1 | 3/3 | Complete | 2026-04-01 |
| 26. Tool 内化与遗留工具收归 | v2.1 | 3/3 | Complete | 2026-04-01 |
| 27. MCP 与 Calculation 原生链路 | v2.1 | 3/3 | Complete | 2026-04-01 |
| 28. src 反向依赖反转与 Consumer 迁移 | v2.1 | 3/3 | Complete | 2026-04-01 |
| 29. 主执行路径切换 | v2.1 | 2/2 | Complete | 2026-04-01 |
| 30. 解耦审计与独立性证明 | v2.1 | 3/3 | Complete | 2026-04-01 |
| 31. Tech Debt Cleanup | v2.1 | 2/2 | Complete | 2026-04-02 |
| 32. Kernel Generator + Tool Runtime v2 核心骨架 | v2.2 | 1/3 | In Progress | - |
| 33. ToolRunner 完整实现 + ToolScheduler | v2.2 | 0/? | Not started | - |
| 34. Exp/Service 接入 + Hook 退役 | v2.2 | 0/? | Not started | - |
| 35. 约束迁移 + ToolRegistry 降级 | v2.2 | 0/? | Not started | - |
| 36. 去总线化 + 高级调度 | v2.2 | 0/? | Not started | - |
