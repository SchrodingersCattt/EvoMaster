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

## Milestone: v2.1 — matmaster/ 完全独立化

**Shipped:** 2026-04-02
**Phases:** 7 | **Plans:** 19

### What Was Built
- Session & Playground 原生化（Session Protocol + LocalSession/SSHSession + 参数化 Playground）
- Tool 全面内化（bash_safety/editor 内联 + MonitorJobTool 迁入 + EvoToolAdapter 删除）
- MCP/Calculation 原生链路（MCPConnection ABC + MCPToolManager + 4 个 calculation 模块搬入）
- src 反向依赖消除（BohriumSetupService 回调注入 + consumer 类型迁移）
- evomaster/ + playground/ + evaluation/ 物理删除（190K+ lines 移除）
- 独立性证明（AST import audit + 隔离测试 + 迁移文档）
- 技术债务收口（32 个测试修复 + 文档同步）

### What Worked
- 分层解耦策略：底层先行（session→tool→MCP→src→主入口→审计→收口），每层为下层建立稳定底座
- 里程碑审计→gap closure 反馈循环：Phase 31 精准定位并修复审计发现的 32 个测试失败
- AST import audit 作为持续回归门禁：每个 phase 结束都能验证是否引入新依赖
- Duck-typing (hasattr) 替代 isinstance 跨包类型检查：避免 import 耦合又保持类型安全
- 回调注入模式（BohriumSetupService）：4 个 callable 替代整个 service 对象，干净切断反向依赖

### What Was Inefficient
- ROADMAP.md 中 Phase 25/26 的 plan 完成数未及时更新（25-03/26-03 有 SUMMARY 但 checkbox 显示 `[ ]`）
- SUMMARY frontmatter 和 REQUIREMENTS.md checkbox 多次不同步，Phase 31 花了专门的 plan 来修复
- v2.0 阶段（12-24）与 v2.1 阶段（25-31）在 gsd-tools 中被统一计数为同一里程碑的 phase，导致归档时需要手动修正

### Patterns Established
- 回调注入模式替代 service 注入：当需要打破反向依赖时，传入 callable 而非整个 service 对象
- Duck-typing 跨包兼容：hasattr 检查替代 isinstance，避免 import 耦合
- 物理删除策略：解耦完成后立即删除死代码，不保留 deprecated 路径
- AST import audit 作为 CI 门禁：验证 matmaster/ 运行时模块无外部依赖
- create_autospec(Protocol, instance=True) 作为 Protocol mock 标准模式

### Key Lessons
1. 大规模解耦应在每个 phase 完成时立即同步文档（checkbox、frontmatter），不要积累到最后
2. 回调注入比依赖注入更适合打破反向依赖——callable 没有 import 负担
3. 物理删除（而非 deprecation）在内部项目中更干净——没有外部用户就不需要过渡期
4. AST-based import audit 比 grep 更可靠——能区分 TYPE_CHECKING 内的 import 和运行时 import
5. 里程碑审计是必要步骤——Phase 31 的 32 个测试失败如果不审计就不会被发现

### Cost Observations
- Model mix: quality profile (opus for planning/execution/verification)
- Sessions: 约 6 个 session 完成 v2.1 全部 7 个 phase
- Notable: 2 天完成 7 阶段 19 个 plan，净删除 81K 行代码

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Phases | Plans | Key Change |
|-----------|--------|-------|------------|
| v1 | 7 | 21 | 契约驱动重构，TDD + 阶段验证反馈循环 |
| v2.1 | 7 | 19 | 分层解耦 + AST import audit 门禁 + 物理删除策略 |

### Cumulative Quality

| Milestone | Tests | Architecture | Zero-Dep Additions |
|-----------|-------|--------------|--------------------|
| v1 | 380 | core/tools/types/hooks/integration/providers | Pydantic v2 (existing), stdlib only |
| v2.1 | 1,294 | +sessions/mcp/calculation/integration (原生化) | paramiko (existing), stdlib only |

### Top Lessons (Verified Across Milestones)

1. 契约优先开发在框架重构中比功能优先开发更有效
2. 阶段验证 + 里程碑审计是发现集成 gap 的关键机制
3. 每个 phase 完成时立即同步文档，不要积累到里程碑末尾
4. 回调注入比 service 注入更适合打破反向依赖
