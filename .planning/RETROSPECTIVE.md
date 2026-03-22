# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

## Milestone: v1 — MatMaster Framework Refactoring

**Shipped:** 2026-03-22
**Phases:** 7 | **Plans:** 21

### What Was Built
- 三层契约类型系统（PlaygroundContext/AgentRuntimeSpec/AgentEvent 16 种事件）
- 纯执行 AgentKernel（4 种终止、Guard 管线、5 Hook 点、流式推理）
- Exp 装配层（ToolRegistry 三源注册、ContextBuilder 分段 prompt、WorkerRegistry Protocol）
- 统一 Playground 类 + config YAML 驱动
- 端到端迁移验证（380 测试全通）
- 目录重组：core/tools/types/ 职责清晰

### What Worked
- TDD 驱动的契约优先开发：先定义 Pydantic frozen model，再实现消费方，类型系统在第一阶段就捕获了设计问题
- TYPE_CHECKING + lazy import 模式有效解决了跨层循环导入
- 阶段级验证（VERIFICATION.md）在 Phase 2/3 发现 gap 并触发 gap closure plan，避免了 debt 累积到集成阶段
- yolo mode + 并行 agent 执行大幅提升了规划和执行效率

### What Was Inefficient
- Phase 3 引入的循环导入需要额外 Plan 04 修复，应在 PLAN 阶段就检测 import 依赖图
- Guard shell 模式（Phase 3）最终在 Phase 6 被删除，说明 shell/placeholder 实现在快速迭代中容易变成 dead code
- 第一次里程碑审计后增加了 Phase 6/7 两个 gap closure 阶段，原始 5-phase 估算不够

### Patterns Established
- Pydantic frozen model + discriminated union 作为层间契约标准
- TYPE_CHECKING guard + lazy import 解决跨包循环导入
- 阶段验证→审计→gap closure 反馈循环
- core/tools/types/hooks/integration/providers 目录按职责组织

### Key Lessons
1. 契约优先：先定义 frozen model 再写实现，类型系统比文档更可靠地约束设计
2. 循环导入是分层架构的天然挑战，需要在 plan 阶段显式设计 import 图
3. Shell/placeholder 实现应谨慎使用——如果不确定何时会填充，不如不创建
4. 5 个核心 Phase + 2 个 gap closure Phase 是合理的里程碑结构
5. Service 层边界（src/ vs matmaster/）的集成 gap 应在早期 Phase 就显式标记为 out of scope 或 in scope

### Cost Observations
- Model mix: quality profile (opus-dominant for planning/verification, sonnet for integration checker)
- Notable: 2 天完成 7 阶段 21 个 plan 的完整生命周期（包括规划、执行、验证、审计）

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Phases | Plans | Key Change |
|-----------|--------|-------|------------|
| v1 | 7 | 21 | 契约驱动重构，TDD + 阶段验证反馈循环 |

### Cumulative Quality

| Milestone | Tests | Architecture | Zero-Dep Additions |
|-----------|-------|--------------|--------------------|
| v1 | 380 | core/tools/types/hooks/integration/providers | Pydantic v2 (existing), stdlib only |

### Top Lessons (Verified Across Milestones)

1. 契约优先开发在框架重构中比功能优先开发更有效
2. 阶段验证 + 里程碑审计是发现集成 gap 的关键机制
