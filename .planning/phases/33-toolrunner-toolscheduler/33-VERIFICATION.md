---
phase: 33-toolrunner-toolscheduler
verified: 2026-04-02T13:00:00Z
status: gaps_found
score: 3/4 success criteria verified
gaps:
  - truth: "CapabilityPolicy 对 effect_level 超限的工具调用返回 deny 决策且附带 guidance（针对实际内建工具）"
    status: partial
    reason: "CapabilityPolicy.evaluate() 检查 spec.effect_level == 'external_write'，但 BUILTIN_META 为 mm_web_search/web_fetch/monitor_job 注入 'external_effect'（非 'external_write'）。效果：对实际内建 web 工具，CapabilityPolicy 的 effect_level 检查永不触发，deny+guidance 路径从 Layer C 消失。CapabilityPolicy 单元测试均使用 'external_write' 直接构造，故所有测试通过，但与 BUILTIN_META 注入值不一致。"
    artifacts:
      - path: "matmaster/tools/tool_catalog.py"
        issue: "BUILTIN_META 中 mm_web_search/web_fetch/monitor_job 的 effect_level 值为 'external_effect'，与 CapabilityPolicy 检查的 'external_write' 不一致"
      - path: "matmaster/core/capability_policy.py"
        issue: "DefaultCapabilityPolicy.evaluate() 检查 spec.effect_level == 'external_write'，但实际内建工具被注入 'external_effect'"
    missing:
      - "将 BUILTIN_META 中 mm_web_search/web_fetch/monitor_job 的 effect_level 值从 'external_effect' 改为 'external_write'（与 ToolSpec 文档注释一致：'pure_read' | 'local_mutation' | 'external_write'）"
      - "或者：将 BUILTIN_META 和 CapabilityPolicy 检查统一使用 'external_effect'，同时更新 ToolSpec 注释"
human_verification:
  - test: "CapabilityPolicy 在 FullToolRunner 链路中对 mm_web_search 的 effect_level 阻断行为"
    expected: "当 RuntimeTopology.active_planes 不含 EXTERNAL_SERVICE 时，mm_web_search 调用应该在 Layer C (CapabilityPolicy) 被拒绝，meta['layer'] = 'policy'"
    why_human: "当前由于 effect_level 值不匹配，该工具在 Layer A (StructuralValidation) 就被拒绝（因 plane 未激活），不会到达 Layer C。需要人工确认：是否接受由 Layer A 代替 Layer C 阻断外部效果工具，还是需要修复让 Layer C 也正确阻断。"
---

# Phase 33: ToolRunner 完整实现 + ToolScheduler 验证报告

**Phase Goal:** 工具执行通过完整的 查找->校验->调度->执行->释放 链路运行，资源调度支持 exclusive/shared_read/counted 三种模式
**Verified:** 2026-04-02T13:00:00Z
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| SC1 | 完整 ToolRunner 执行链 ToolCatalog 查找 -> StructuralValidation -> RunStateGuard -> CapabilityPolicy -> ToolScheduler -> executor -> 释放 端到端可运行 | VERIFIED | FullToolRunner 七步执行链实现完整（tool_runner.py L178-324），所有 9 个 TestXxx 测试类通过 |
| SC2 | ToolScheduler 对 exclusive 资源实现互斥调度，对 shared_read 资源实现并发调度，对 counted 资源实现信号量控制 | VERIFIED | _RWLock + asyncio.Semaphore 实现完整（tool_scheduler.py），6 个测试类 10 个测试全部通过 |
| SC3 | StructuralValidation 对 args_schema 校验失败 / plane 未启用 / session_capabilities 不匹配的工具调用返回 deny 决策 | VERIFIED | 三步校验逻辑完整（structural_validation.py L52-94），11 个测试（TestArgsSchema/TestPlaneCheck/TestCapabilities）全部通过 |
| SC4 | CapabilityPolicy 对 effect_level 超限或 capability 不匹配的工具调用返回 deny 决策且附带 guidance | PARTIAL | CapabilityPolicy 单元测试使用 effect_level='external_write' 均通过，但 BUILTIN_META 为内建 web 工具注入 'external_effect'，导致对真实内建工具 effect_level 检查永不触发 |

**Score:** 3/4 success criteria verified

---

### Required Artifacts

| Artifact | Min Lines | Actual Lines | Status | Details |
|----------|-----------|--------------|--------|---------|
| `matmaster/core/structural_validation.py` | 40 | 94 | VERIFIED | StructuralValidation 三步校验，jsonschema.validate 集成 |
| `matmaster/core/capability_policy.py` | 40 | 113 | VERIFIED | @runtime_checkable CapabilityPolicy Protocol + DefaultCapabilityPolicy |
| `matmaster/core/tool_scheduler.py` | 100 | 199 | VERIFIED | _RWLock + ToolScheduler + SchedulerTicket，纯 asyncio 原语 |
| `matmaster/core/tool_runner.py` | 200 | 324 | VERIFIED | FullToolRunner + InlineToolRunner，保留现有实现 |
| `matmaster/tools/tool_catalog.py` | 80 | 147 | VERIFIED (with warning) | BUILTIN_CLAIMS/BUILTIN_META 查表，get_tool() 增强；但 BUILTIN_META effect_level 值与 CapabilityPolicy 检查不一致 |
| `tests/matmaster/core/test_structural_validation.py` | 60 | 243 | VERIFIED | 11 测试，TestArgsSchema/TestPlaneCheck/TestCapabilities |
| `tests/matmaster/core/test_capability_policy.py` | 50 | 228 | VERIFIED | 11 测试，TestEffectLevel/TestCapabilityMatch/TestProtocol |
| `tests/matmaster/core/test_tool_scheduler.py` | 100 | 198 | VERIFIED | 10 测试，TestExclusive/TestSharedRead/TestCounted/TestTimeout/TestMultiResource/TestCountedLimitNone |
| `tests/matmaster/core/test_full_tool_runner.py` | 120 | 519 | VERIFIED | 13 测试，9 个测试类，全链路覆盖 |
| `tests/matmaster/core/test_builtin_claims.py` | 40 | 214 | VERIFIED | 15 测试，所有内建工具 ResourceClaim 声明验证 |

---

### Key Link Verification

| From | To | Via | Pattern | Status | Details |
|------|----|-----|---------|--------|---------|
| tool_runner.py FullToolRunner | tool_catalog.py | catalog.get_tool() | `self._catalog.get_tool` | VERIFIED | 存在且调用 |
| tool_runner.py FullToolRunner | structural_validation.py | validation.validate() | `self._validation.validate` | VERIFIED | 存在且调用 |
| tool_runner.py FullToolRunner | guard_pipeline.py | guard_pipeline.evaluate() | `self._guard_pipeline.evaluate` | VERIFIED | 存在且调用 |
| tool_runner.py FullToolRunner | capability_policy.py | policy.evaluate() | `self._policy.evaluate` | VERIFIED | 存在且调用 |
| tool_runner.py FullToolRunner | tool_scheduler.py | scheduler.acquire/release | `await self._scheduler.acquire`, `await self._scheduler.release` | VERIFIED | 两处均存在且 await |
| structural_validation.py | tool_decision.py | 返回 ToolDecision | `ToolDecision(decision=` | VERIFIED | 3 处 deny + 1 处 allow |
| structural_validation.py | jsonschema | args_schema 校验 | `jsonschema.validate` | VERIFIED | 2 处（validate 调用 + 异常捕获） |
| capability_policy.py | tool_decision.py | 返回 ToolDecision | `ToolDecision(decision=` | VERIFIED | deny 含 guidance= |
| tool_catalog.py | tool_spec.py ResourceClaim | BUILTIN_CLAIMS 查表 | `BUILTIN_CLAIMS.get(tool_name, ())` | VERIFIED | 存在，16 个内建工具声明 |

---

### Data-Flow Trace (Level 4)

这些是纯逻辑/调度模块（无 UI 渲染、无异步数据流），跳过 Level 4 数据流追踪。

关键数据路径验证：

| 路径 | 起点 | 终点 | 状态 |
|------|------|------|------|
| tool_catalog.get_tool() 返回 ToolInstance | BUILTIN_CLAIMS/BUILTIN_META 查表 | ToolInstance.tool_binding.resource_claims | VERIFIED：测试 TestBashClaim/TestReadClaim/TestWriteClaim 等验证注入正确 |
| ToolScheduler.acquire() 互斥调度 | claims tuple | SchedulerTicket | VERIFIED：TestExclusive 验证第二个 exclusive 在持有期间 timeout 返回 None |
| FullToolRunner 七步链路 | tc.name → catalog → executor | ToolResult with meta["layer"] | VERIFIED：9 个测试类验证每层 deny/error 路径 |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| 所有 Phase 33 测试通过 | `uv run pytest tests/matmaster/core/test_structural_validation.py tests/matmaster/core/test_capability_policy.py tests/matmaster/core/test_tool_scheduler.py tests/matmaster/core/test_full_tool_runner.py tests/matmaster/core/test_builtin_claims.py -q` | 73 passed in 0.65s | PASS |
| isinstance(FullToolRunner(...), ToolRunner) | Python 直接执行 | True | PASS |
| InlineToolRunner 回归测试 | `uv run pytest tests/matmaster/core/test_tool_runner.py -q` | 13 passed | PASS |
| BUILTIN_META effect_level 值一致性 | 对比 BUILTIN_META 与 CapabilityPolicy 检查字符串 | mm_web_search='external_effect' vs check='external_write' | FAIL — 见 Gap |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| TCON-01 | 33-01-PLAN.md | 实现 StructuralValidation（无状态），负责 args_schema 校验 / plane 启用检查 / session_capabilities 匹配 | SATISFIED | structural_validation.py 三步校验完整，11 个测试通过 |
| TCON-03 | 33-01-PLAN.md | 实现 CapabilityPolicy Protocol，Phase 1 处理 effect_level 约束和 plane/capability 匹配 | PARTIALLY SATISFIED | Protocol + DefaultCapabilityPolicy 实现正确，unit tests 通过；但 BUILTIN_META 注入值与 CapabilityPolicy 检查值不一致，导致对内建工具 effect_level 路径不触发 |
| TRUN-04 | 33-02-PLAN.md | 实现 ToolScheduler，基于 ResourceClaim 调度（exclusive 互斥 / shared_read 并发 / counted 信号量），支持 fast path 跳过 | SATISFIED | tool_scheduler.py 199 行，10 个测试全部通过 |
| TRUN-03 | 33-03-PLAN.md | 实现完整 ToolRunner，执行链为 ToolCatalog 查找 → StructuralValidation → RunStateGuard → CapabilityPolicy → fast path 判定 → ToolScheduler → executor → 释放 | SATISFIED | FullToolRunner 七步链实现完整，13 个测试通过，isinstance 检查通过 |

**孤立需求检查：** REQUIREMENTS.md 中 TRUN-03/TRUN-04/TCON-01/TCON-03 均已被各 PLAN 文件声明，无孤立需求。

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `matmaster/tools/tool_catalog.py` | 57-60 | BUILTIN_META 使用 `"external_effect"` 而非 `"external_write"` 作为 web 工具的 effect_level | WARNING | CapabilityPolicy 的 effect_level 检查（`== "external_write"`）对内建 web 工具永不触发，使 Layer C 的 effect_level 守卫对真实工具失效 |
| `matmaster/tools/tool_catalog.py` | 46-54 | BUILTIN_META 使用 `"none"` 而非 `"pure_read"` 作为只读工具的 effect_level | INFO | ToolSpec 注释写的有效值是 `"pure_read" | "local_mutation" | "external_write"`，但 ToolRunner fast path 检查 `== "none"` 而非 `"pure_read"`。实际上 fast path 正常工作，但存在命名不一致 |

---

### Human Verification Required

#### 1. Layer C effect_level 阻断行为确认

**Test:** 构造一个 RuntimeTopology（active_planes 不含 EXTERNAL_SERVICE），使用实际 ToolCatalog.get_tool("mm_web_search") 获取工具实例，通过 FullToolRunner.execute_batch() 执行，观察 ToolResult.meta["layer"] 的值

**Expected（如果 BUILTIN_META 正确）:** meta["layer"] = "policy"（由 CapabilityPolicy 拒绝）

**Actual（当前）:** meta["layer"] = "structural"（由 StructuralValidation 的 plane 检查拒绝，因为 EXTERNAL_SERVICE plane 未激活）

**Why human:** 需要确认是否接受当前行为（Layer A 代替 Layer C 阻断外部效果工具），或者需要修复 BUILTIN_META 的 effect_level 值使 Layer C 也能正确识别并拒绝。这是语义层面的判断，涉及两层防御的设计意图。

---

### Gaps Summary

**一个 partial gap，一个信息级不一致：**

**Gap（TCON-03 partial）：** `BUILTIN_META` 为内建 web 工具（`mm_web_search`、`web_fetch`、`monitor_job`）注入的 `effect_level` 值为 `"external_effect"`，而 `DefaultCapabilityPolicy.evaluate()` 检查 `spec.effect_level == "external_write"`。两个值不匹配，导致 CapabilityPolicy 的 effect_level 守卫对这些实际内建工具永不触发。

**修复方向（二选一）：**
1. 将 `BUILTIN_META` 中 web 工具的值从 `"external_effect"` 改为 `"external_write"`（与 `ToolSpec` 注释文档一致）
2. 将 `DefaultCapabilityPolicy` 的检查从 `== "external_write"` 改为 `== "external_effect"`，并同步更新 `ToolSpec` 注释

**影响范围：** 不影响测试通过（所有 73 个 Phase 33 测试均通过），不影响链路完整性（web 工具仍被 StructuralValidation 的 plane 检查阻断），不影响 ToolScheduler 和 fast path 功能。仅影响 TCON-03 中 effect_level 策略层的语义正确性。

**Info（命名不一致）：** `ToolSpec` 注释 valid values 为 `"pure_read" | "local_mutation" | "external_write"`，但 `BUILTIN_META` 注入 `"none"` 用于只读工具，`ToolRunner` fast path 检查 `== "none"`。fast path 功能正常工作，但 `"none"` 不在 `ToolSpec` 的文档枚举中（应为 `"pure_read"`）。

---

*Verified: 2026-04-02T13:00:00Z*
*Verifier: Claude (gsd-verifier)*
