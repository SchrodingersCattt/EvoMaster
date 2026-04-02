# Roadmap: MatMaster Framework Evolution

## Milestones

- ✅ **v1 MatMaster Framework Refactoring** - Phases 1-7 (shipped 2026-03-22)
- ✅ **v1.1 Agent 外围能力构建** - Phases 8-11 (shipped 2026-03-25)
- ✅ **v2.0 matmaster 协程改造** - Phases 12-24 (shipped 2026-03-30)
- ✅ **v2.1 matmaster/ 完全独立化** — Phases 25-31 (shipped 2026-04-02)

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

## Progress

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
