# Phase 33: ToolRunner 完整实现 + ToolScheduler - Context

**Gathered:** 2026-04-02
**Status:** Ready for planning

<domain>
## Phase Boundary

工具执行从 InlineToolRunner 的简单 guard→execute 链路，升级为完整的 ToolCatalog 查找 → StructuralValidation → RunStateGuard → CapabilityPolicy → fast path 判定 → ToolScheduler → executor → 释放 执行链。ToolScheduler 支持 exclusive/shared_read/counted 三种资源调度模式。StructuralValidation 实现无状态参数校验。CapabilityPolicy Phase 1 实现 effect_level 约束和 plane/capability 匹配。

</domain>

<decisions>
## Implementation Decisions

### Hook 策略
- **D-01:** 完整 ToolRunner 不调用 pre_hook/post_hook。Hook 系统之后完全重做，Phase 33 不考虑任何 Hook 兼容性。这是独立开发项目，无外部消费者依赖。

### Scheduler 并发原语
- **D-02:** RWLock 用 asyncio.Lock + asyncio.Condition + 读者计数器组合实现经典读写锁。counted 模式直接用 asyncio.Semaphore。不引入第三方锁库。
- **D-03:** Scheduler acquire 默认超时 60 秒。超时返回 ToolResult(status="error", meta={"layer": "scheduler"})。

### StructuralValidation 校验
- **D-04:** args_schema 校验使用 jsonschema 库（已是项目依赖，v4.26）完整校验。校验错误信息直接作为 ToolDecision.reason 返回给 LLM。

### Spec 锁定（直接执行）
- **D-05:** 完整 ToolRunner 执行链严格遵循 spec §9.1：Catalog 查找 → StructuralValidation → RunStateGuard → CapabilityPolicy → fast path → Scheduler → executor → 释放。
- **D-06:** 错误处理严格遵循 spec §9.3：每层统一产出 ToolResult，meta["layer"] 标记失败来源。
- **D-07:** CapabilityPolicy Phase 1 仅处理 effect_level 约束和 plane/capability 匹配，不迁移工具内部安全检查。
- **D-08:** Fast path 条件：effect_level="none" + claims 全 shared_read + fast_path_eligible=True → 跳过 Scheduler，不跳过 CapabilityPolicy。
- **D-09:** 内建工具 ResourceClaim 按 spec §8.2 表格声明。
- **D-10:** 激活路径：Exp.build_runtime() 构造完整 ToolRunner（含 ToolCatalog + StructuralValidation + GuardPipeline + CapabilityPolicy + ToolScheduler），通过 AgentRuntimeSpec.tool_runner 注入 Kernel。

### D-10 范围修正（2026-04-02 Review 后）
- **D-10 (revised):** D-10 原文描述的是激活机制（how），不是激活时机（when）。经 GPT cross-review 确认：Phase 33 仅实现+测试 FullToolRunner 执行链（通过直接构造的集成测试验证），**不改 Exp.build_runtime()**。Exp 注入 FullToolRunner 移至 Phase 34（ESIN-04）。
  - 原因 1: 当前 `_run_items()` 不 yield ToolCallEvent/ToolResultEvent（Phase 32 gap），FullToolRunner 默认化后 run_stream() 缺 tool 事件
  - 原因 2: FullToolRunner 不调 hook（D-01），Service 层仍依赖 Hook→Bus 事件链，默认化会断掉 WorkspaceHandler/ChatHistory/SSE 事件流
  - 原因 3: on_skill_hit 仍直走 registry.register()，不走 catalog.register_overlay()，catalog.version 不递增导致 MCP 工具对模型不可见

### Claude's Discretion
- Scheduler 内部 RWLock 的具体实现细节（公平性策略、饥饿防护）
- StructuralValidation 的路径规范化具体实现
- CapabilityPolicy 的具体拒绝规则实现（在 spec 约束范围内）

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 设计 Spec（核心）
- `docs/specs/2026-04-02-tool-runtime-v2.md` — Tool Runtime v2 完整架构设计。Phase 33 重点关注：§6.9 三层约束模型、§8 调度模型、§9 工具执行主链、§10 内建工具总表、§13 分阶段实施计划、§14 文件布局
- `docs/specs/2026-04-02-kernel-generator-first.md` — Kernel generator-first 设计，Phase 33 需理解 _run_items() 如何调用 ToolRunner

### Phase 32 产出（已实现的基础设施）
- `matmaster/core/tool_runner.py` — ToolRunner Protocol + InlineToolRunner（Phase 32 实现），Phase 33 在此文件中实现完整 ToolRunner
- `matmaster/tools/tool_catalog.py` — ToolCatalog Phase 1 facade（Phase 32 实现），Phase 33 可能需要扩展
- `matmaster/types/tool_spec.py` — ToolSpec / ResourceClaim / ToolBinding / ToolInstance 定义
- `matmaster/types/tool_decision.py` — ToolDecision 定义
- `matmaster/types/topology.py` — RuntimeTopology / SessionCapabilities / ToolPlane 定义

### 现有约束层代码
- `matmaster/core/guard_pipeline.py` — GuardPipeline.evaluate()，完整 ToolRunner 的 Layer B 复用此接口
- `matmaster/core/agent.py` — AgentKernel，理解 _run_items() 中 tool_runner 的调用方式

### 需求定义
- `.planning/REQUIREMENTS.md` — Phase 33 映射的 4 个 requirement（TRUN-03, TRUN-04, TCON-01, TCON-03）

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `matmaster/core/tool_runner.py`: ToolRunner Protocol（execute_batch 接口）+ ToolExecutionContext 已定义，完整实现在同文件新增
- `matmaster/tools/tool_catalog.py`: ToolCatalog.get_tool() 返回 ToolInstance，完整 ToolRunner 直接消费
- `matmaster/core/guard_pipeline.py`: GuardPipeline.evaluate() 接口不变，完整 ToolRunner 的 Layer B 直接调用
- `matmaster/types/tool_decision.py`: ToolDecision 已定义，StructuralValidation 和 CapabilityPolicy 返回此类型
- `matmaster/types/tool_spec.py`: ResourceClaim 已定义（resource_id, mode, limit），Scheduler 直接消费

### Established Patterns
- frozen Pydantic model 用于层间契约 — StructuralValidation 和 CapabilityPolicy 返回 frozen ToolDecision
- `@runtime_checkable Protocol` 用于接口定义 — CapabilityPolicy 遵循同样模式（spec §6.9）
- jsonschema 已是项目依赖（v4.26）— StructuralValidation 直接使用

### Integration Points
- `matmaster/core/agent.py` _run_items(): `tool_runner = spec.tool_runner or InlineToolRunner(spec, guards)` — Phase 33 后 spec.tool_runner 由 Exp 注入完整 ToolRunner
- `matmaster/core/exp.py` build_runtime(): 需要新增完整 ToolRunner 构造逻辑
- 新增文件（按 spec §14）：`matmaster/core/tool_scheduler.py`、`matmaster/core/structural_validation.py`、`matmaster/core/capability_policy.py`

</code_context>

<specifics>
## Specific Ideas

- ToolScheduler 的 RWLock 实现约 40-60 行，基于 asyncio.Lock + asyncio.Condition + 读者计数器的经典模式
- spec §8.2 内建工具 ResourceClaim 表是调度行为的完整定义，实现时应确保每个内建工具的 ToolBinding 正确声明 resource_claims
- spec §9.2 fast path 的三个条件（effect_level + claims mode + fast_path_eligible）必须同时满足
- StructuralValidation 的 jsonschema 校验错误应提取为人类可读的 reason，帮助 LLM 修正参数

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 33-toolrunner-toolscheduler*
*Context gathered: 2026-04-02*
