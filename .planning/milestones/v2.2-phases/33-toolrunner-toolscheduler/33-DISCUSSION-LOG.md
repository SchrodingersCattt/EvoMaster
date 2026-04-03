# Phase 33: ToolRunner 完整实现 + ToolScheduler - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-02
**Phase:** 33-toolrunner-toolscheduler
**Areas discussed:** pre_hook 过渡期处理, Scheduler 实现细节, args_schema 校验库

---

## Spec 核对

用户要求先完整查阅 `docs/specs/2026-04-02-tool-runtime-v2.md`，核对设计方案后仅讨论 spec 未覆盖的内容。

**Spec 已覆盖（无需讨论）：**
- 激活策略（§5.2: Exp.build_runtime() 构造）
- 执行链（§9.1: 完整七步链路）
- 错误处理（§9.3: 每层统一 ToolResult）
- CapabilityPolicy Phase 1 职责（§6.9 Layer C）
- 内建工具 ResourceClaim 表（§8.2）
- Fast path 规则（§9.2）

**Spec 未覆盖（进入讨论）：**
- pre_hook 在 Phase 33-34 过渡期的处理方式
- Scheduler RWLock 实现原语和超时默认值
- args_schema 校验使用的库

---

## pre_hook 过渡期处理

| Option | Description | Selected |
|--------|-------------|----------|
| 完整链路内插入 pre_hook | CapabilityPolicy 之后、Scheduler acquire 之前插入 run_pre_tool_call | |
| Kernel 层外部包装 | ToolRunner 不管 hook，Kernel 在调用前先跑 pre_hook | |
| 不调 pre_hook | 按 spec 终态实现，Phase 33 仅测试验证 | |
| (Other) 暂时去掉所有 hook | Hook 系统之后完全重做，独立开发项目不考虑兼容性 | ✓ |

**User's choice:** 暂时去掉所有 hook，hook 系统之后完全重做，这是独立开发项目，不需要考虑任何兼容问题
**Notes:** 这意味着完整 ToolRunner 不调用 pre_hook/post_hook，直接按 spec 终态实现

---

## Scheduler RWLock 实现方案

| Option | Description | Selected |
|--------|-------------|----------|
| asyncio.Condition 组合 | asyncio.Lock + asyncio.Condition + 读者计数器，经典 RWLock | ✓ |
| asyncio.Lock 简化版 | exclusive 和 shared_read 都用同一个 Lock，牺牲读并发 | |
| Claude 自定 | 满足语义即可 | |

**User's choice:** asyncio.Condition 组合
**Notes:** 无

---

## Scheduler acquire 超时默认值

| Option | Description | Selected |
|--------|-------------|----------|
| 30 秒 | 中等宽松 | |
| 60 秒 | 适合 HPC 场景长时操作 | ✓ |
| Claude 自定 | 合理默认值即可 | |

**User's choice:** 60 秒
**Notes:** 无

---

## args_schema 校验库

| Option | Description | Selected |
|--------|-------------|----------|
| jsonschema 库校验 | jsonschema.validate() 完整校验，已是项目依赖 v4.26 | ✓ |
| 基本类型检查 | 仅 required + type，轻量但不完整 | |
| Claude 自定 | 决定校验深度 | |

**User's choice:** jsonschema 库校验
**Notes:** 无

---

## Claude's Discretion

- Scheduler RWLock 具体实现细节（公平性、饥饿防护）
- StructuralValidation 路径规范化实现
- CapabilityPolicy 具体拒绝规则

---

## D-10 范围修正 (2026-04-02 Post-Review)

GPT cross-review 指出 4 个硬耦合点。Claude 验证代码后确认其中 3 个属实：

| Issue | GPT 判断 | Claude 代码验证 | 归属 Phase |
|-------|----------|----------------|------------|
| FullToolRunner 默认化断 Hook 事件链 | 正确 | `_run_items()` 未 yield ToolCallEvent/ToolResultEvent（Phase 32 gap） | Phase 34 (KGEN-06 + ESIN-04) |
| on_skill_hit 不走 catalog overlay | 正确 | `exp.py:509` 直调 `registry.register()` | Phase 34 (ESIN-05) |
| source 归一化 | 正确 | `agent.py:264` 用 `source="agent"`，ChatHistory 过滤 `MatMaster` | Phase 34 (ESIN-06) |
| ContextBuilder tool listing | 正确但已缓解 | `ToolCatalog.register_overlay()` 内部走 registry，listing 同步 | Phase 35 (CMIG-05) |

**Decision**: Exp.build_runtime() 注入 FullToolRunner 从 Phase 33 移至 Phase 34（ESIN-04）。Phase 33 只实现+测试执行链。

## Deferred Ideas

None
