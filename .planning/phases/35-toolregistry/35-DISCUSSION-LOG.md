# Phase 35: 约束迁移 + ToolRegistry 降级 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-03
**Phase:** 35-toolregistry
**Areas discussed:** 约束迁移边界, ToolRegistry 降级程度, ContextBuilder 处置, state_mode/stop_mode

---

## 约束迁移边界

### read-before-modify 迁移后工具内部处置

| Option | Description | Selected |
|--------|-------------|----------|
| 完全删除 | 工具内部删除 tracker 引用和检查逻辑，ReadTracker 仅通过 GuardContext 注入 RunStateGuard。工具变纯粹。符合 spec 设计意图。 | ✓ |
| 双重检查保留 | Guard 层新增检查，但工具内部也保留作为 defense-in-depth。违背 spec 的"安全约束不散落在工具内部"设计原则。 | |

**User's choice:** 完全删除
**Notes:** 无附加说明

### bash 危险命令 + Python 内容检查迁移范围

| Option | Description | Selected |
|--------|-------------|----------|
| 一起迁移 | is_dangerous_bash_command 和 is_dangerous_python_content 都迁入 CapabilityPolicy。bash_tool.py 变为纯执行层。 | ✓ |
| 只迁 bash，Python 保留 | Python 内容检查是 BashTool 特有的输入过滤，保留在工具内部作为 input validation。 | |
| 全部保留 | Phase 35 只在 CapabilityPolicy 新增等价检查，工具内部不删。降低迁移风险但违背 spec。 | |

**User's choice:** 一起迁移
**Notes:** 无附加说明

### ReadTracker 生命周期与注入路径

| Option | Description | Selected |
|--------|-------------|----------|
| ReadTracker 存在 GuardContext | ReadTracker 实例在 Exp.build_runtime() 创建，注入 RunStateGuard。ReadTool 仍持有 tracker 引用调用 mark_read()。Guard 层检查 has_been_read()。符合 spec "read_tracker 注入 GuardContext"。 | ✓ |
| ReadTracker 挂在 ToolRunner | ReadTracker 由 ToolRunner 持有，在 execute_batch 前后检查和更新。职责影又越界。 | |
| ReadTracker 作为 ToolCatalog 层级状态 | ReadTracker 挂在 ToolCatalog，作为 run-scoped state。ToolCatalog 当前无状态，会引入状态。 | |

**User's choice:** ReadTracker 存在 GuardContext
**Notes:** 无附加说明

---

## ToolRegistry 降级程度

### 降级策略

| Option | Description | Selected |
|--------|-------------|----------|
| 激进删除 | 删除 execute()、get_tool_definitions()、get_tools_by_source()。只保留 register()、all_tools、__contains__、__len__ 等纯存储接口。断点干净。 | ✓ |
| 保守标 deprecated | 保留所有方法但标 @deprecated，加 DeprecationWarning。给测试和外部消费者过渡期。但实际上没有外部消费者。 | |

**User's choice:** 激进删除
**Notes:** 无附加说明

### AgentRuntimeSpec.tool_registry 字段处置

| Option | Description | Selected |
|--------|-------------|----------|
| 删除字段 | AgentRuntimeSpec 不再持有 tool_registry。Kernel 仅通过 spec.tool_runner + spec.tool_catalog 消费工具。Kernel legacy fallback 同步删除。 | ✓ |
| 保留但标可选 | 字段保留为 Optional，但 Kernel 不再使用。仅供 ToolCatalog 内部存储层引用。延迟清理到 v2.3。 | |

**User's choice:** 删除字段
**Notes:** 无附加说明

---

## ContextBuilder 处置

### _build_tools() 处理方案

| Option | Description | Selected |
|--------|-------------|----------|
| 删除工具枚举段 | 按 spec §6.9 建议，移除工具逐行枚举，改为通用说明。消除 system prompt 与 tool_definitions 的不一致风险。ContextBuilder 不再依赖 tool_registry。 | ✓ |
| 迁移到 ToolCatalog | 保留工具枚举段，但数据源从 tool_registry.all_tools 改为 tool_catalog.list_tools()。overlay 动态注入后 system prompt 不会同步更新。 | |

**User's choice:** 删除工具枚举段
**Notes:** 无附加说明

---

## state_mode/stop_mode

### 枚举值对齐

| Option | Description | Selected |
|--------|-------------|----------|
| 按 spec 纠正 | state_mode 改为 Literal["stateless", "persistent"]，stop_mode 改为 Literal["cancellable", "best_effort", "non_cancellable"]。当前代码值未被消费，纠正代价为零。 | ✓ |
| 保留当前代码值 | 当前 turn_scoped/session_scoped/immediate/graceful/detached 更细粒度，保留并更新 spec 来对齐。但当前没有消费方。 | |

**User's choice:** 按 spec 纠正
**Notes:** 无附加说明

### 启用范围

| Option | Description | Selected |
|--------|-------------|----------|
| 本 phase 一步到位 | Phase 35 同时完成 ToolCompiler 填充 + Scheduler 消费。Scheduler 已有 stop_event 路径，改动量可控。CMIG-03 完整交付。 | ✓ |
| 拆分到 Phase 36 | Phase 35 只填充字段，Scheduler 消费推到 Phase 36（高级调度）。减小 Phase 35 scope 但 CMIG-03 变跨 phase。 | |

**User's choice:** 本 phase 一步到位
**Notes:** 无附加说明

---

## Claude's Discretion

- RunStateGuard 的具体实现结构
- CapabilityPolicy 中 bash/python 检查的具体分发逻辑
- ToolCompiler 中各内建工具的 state_mode/stop_mode 具体取值映射
- Scheduler 对 best_effort/non_cancellable 的具体行为差异实现
- stop_event 注入路径调整
- 测试文件组织方式

## Deferred Ideas

None
