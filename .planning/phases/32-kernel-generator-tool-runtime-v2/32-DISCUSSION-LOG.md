# Phase 32: Kernel Generator + Tool Runtime v2 核心骨架 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-02
**Phase:** 32-kernel-generator-tool-runtime-v2
**Areas discussed:** Plan 拆分策略, ToolResult 迁移路径, Spec 锁定确认, 测试组织策略

---

## Plan 拆分策略

| Option | Description | Selected |
|--------|-------------|----------|
| 3 个 plan | Plan A: 类型体系+ToolResult → Plan B: ToolCatalog+ToolRunner+Spec → Plan C: Kernel generator+回归 | ✓ |
| 2 个 plan | 基础设施一次性就位 → Kernel generator+回归。单 plan 较大 | |
| 4 个 plan | 类型体系 → ToolResult → ToolCatalog+ToolRunner → Kernel generator。粒度细但上下文切换多 | |

**User's choice:** 3 个 plan（推荐）
**Notes:** 每个 plan 可独立验证。Plan A 纯类型定义无运行时影响，Plan B 建立执行基础设施，Plan C 改造 Kernel 核心循环。

---

## ToolResult 迁移路径

| Option | Description | Selected |
|--------|-------------|----------|
| 一步到位替换 | 直接删 info，新增 payload+meta。影响面仅 3 处代码+测试 | ✓ |
| 兼容过渡 | 保留 info 作 deprecated alias（@property 指向 payload），同时新增 payload+meta | |
| 只加不删 | 保留 info 不动，新增 payload+meta 作额外字段。等 Phase 35 统一清理 | |

**User's choice:** 一步到位替换（推荐）
**Notes:** 经 grep 确认 result.info 在 matmaster/ 内仅有 3 处直接消费（output_processor.py, hooks.py, events.py），迁移量极小。

---

## Spec 锁定确认

### AgentRuntimeSpec 字段类型

| Option | Description | Selected |
|--------|-------------|----------|
| 具体类型 | 直接用 ToolRunner \| None 等具体类型注解，TYPE_CHECKING 解决循环导入 | ✓ |
| Any \| None | Spec 原案，Phase 1 用 Any 占位 | |

**User's choice:** 具体类型（推荐）
**Notes:** Phase 32 同时定义类型体系和扩展 spec，Any 的前提（类型尚未定义）不成立。

### ToolCatalog facade 边界

| Option | Description | Selected |
|--------|-------------|----------|
| 纯委托 facade | 所有操作委托给内部 ToolRegistry，get_tool() 包装为 ToolInstance | ✓ |
| 双轨并存 | base 用真正 dict[str, ToolInstance]，overlay 用 registry | |

**User's choice:** 纯委托 facade（spec 原案）
**Notes:** 保持 Phase 1 改动面最小，ContextBuilder/SkillTool/MCP 注入路径不动。

### 其他 Spec 设计点

| Option | Description | Selected |
|--------|-------------|----------|
| Spec 原案确认 | 全部设计点锁定可直接执行 | ✓ |
| 有调整 | 需要调整某些设计点 | |

**User's choice:** Spec 原案确认
**Notes:** _KernelItem 结构、ToolExecutionContext、InlineToolRunner、Hook 并存、取消机制、LLM 事件 yield 策略全部确认。

---

## 测试组织策略

| Option | Description | Selected |
|--------|-------------|----------|
| 跟随源码结构 | 类型测试→types/，ToolCatalog→tools/，ToolRunner→core/，kernel 测试→core/ | ✓ |
| 集中新目录 | 创建 tests/matmaster/tool_runtime_v2/ 集中放置 | |
| Claude 决定 | 自行判断每个测试放最合适位置 | |

**User's choice:** 跟随源码结构（推荐）
**Notes:** 保持现有测试目录约定一致性。

---

## Claude's Discretion

None — all decisions were made by the user.

## Deferred Ideas

None — discussion stayed within phase scope.
