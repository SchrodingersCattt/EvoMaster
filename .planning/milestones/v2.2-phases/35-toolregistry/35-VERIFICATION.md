---
phase: 35-toolregistry
verified: 2026-04-03T08:00:00Z
status: passed
score: 13/13 must-haves verified
re_verification:
  previous_status: gaps_found
  previous_score: 11/13
  gaps_closed:
    - "edit_file 调用在目标文件未被 read 过时被 RunStateGuard 拒绝 — exp.py:L269 已修正为 GuardPipeline(guards, read_tracker=self._read_tracker)"
    - "agent.py _run_loop() 中创建但从不调用的 guard_pipeline 死代码已清除"
  gaps_remaining: []
  regressions: []
---

# Phase 35: 约束迁移 + ToolRegistry 降级 Verification Report

**Phase Goal:** 工具安全检查从工具内部分散逻辑统一迁入三层约束模型，ToolRegistry 降级为纯存储后 ToolCatalog 成为唯一上层消费接口
**Verified:** 2026-04-03T08:00:00Z
**Status:** passed
**Re-verification:** Yes — after gap closure (previous status: gaps_found, score 11/13)

---

## Goal Achievement

### Success Criteria (from ROADMAP.md)

| # | Success Criterion | Status | Evidence |
|---|------------------|--------|---------|
| 1 | WriteTool/EditTool 中的 read-before-modify 检查代码已删除，等价逻辑由 RunStateGuard + ReadTracker 完成 | VERIFIED | 代码已从工具中删除 (VERIFIED)；exp.py:L269 已修正注入 read_tracker，ReadBeforeModifyGuard 生产路径生效（行为测试：unread file -> allowed=False, read file -> allowed=True） |
| 2 | BashTool 中的 `_is_dangerous_command` 检查代码已删除，等价逻辑由 CapabilityPolicy 完成 | VERIFIED | bash_tool.py 中相关 patterns/functions 已全部删除，capability_policy.py 包含完整等价实现 |
| 3 | ToolBinding 的 state_mode/stop_mode 字段被 Scheduler 实际消费，cancel 策略随之调整 | VERIFIED | FullToolRunner cancel check 已在 L290-302 实现 stop_mode-aware 分支 |
| 4 | ToolRegistry 不再被 ContextBuilder / SkillTool / MCP 注入路径直接调用 | VERIFIED | ToolRegistry 仅剩 4 个纯存储方法，agent.py 零 spec.tool_registry 引用 |
| 5 | ContextBuilder 工具来源从 tool_registry.all_tools 迁移，或移除 system prompt 工具枚举段 | VERIFIED | _build_tools() 返回通用 "function calling" 说明，不再枚举工具 |

---

### Observable Truths (from Plan must_haves)

#### Plan 01 Truths

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | edit_file 调用在目标文件未被 read 过时被 RunStateGuard 拒绝，返回 deny + guidance | VERIFIED | exp.py:L269 已为 GuardPipeline(guards, read_tracker=self._read_tracker)；行为测试确认 unread file -> allowed=False, reason="File 'test.py' must be read before modify" |
| 2 | write_file 对已有文件在未 read 过时被 input_validator 拒绝 | VERIFIED | WriteTool.validate_input() (L58-88) 含 has_been_read 检查，ToolCompiler 正确绑定 input_validator |
| 3 | WriteTool._execute() 和 EditTool._execute() 内部不再包含 read-before-modify 检查代码 | VERIFIED | write_tool.py _execute() 起始于 L90，has_been_read 在 L80 属于 validate_input 作用域；edit_tool.py 全文无 has_been_read |
| 4 | execute_bash 传入危险命令时被 CapabilityPolicy 拒绝，返回 deny + guidance | VERIFIED | capability_policy.py 含完整实现，17 tests pass（含 bash_safety 测试） |
| 5 | BashTool._execute()/_execute_async() 内部不再包含 is_dangerous_bash_command 调用 | VERIFIED | bash_tool.py 全文无 is_dangerous 引用 |
| 6 | bash_tool.py 中 _DANGEROUS_COMMAND_PATTERNS 和相关函数已删除 | VERIFIED | bash_tool.py 全文无 _DANGEROUS_COMMAND_PATTERNS/is_dangerous_python_content |

#### Plan 02 Truths

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 7 | ToolCompiler 为每个内建工具填充 state_mode/stop_mode（不再使用默认值） | VERIFIED | BUILTIN_STOP_MODES 覆盖 16 个工具，compile() 从映射表读取 |
| 8 | FullToolRunner 根据 stop_mode 区分取消行为 | VERIFIED | tool_runner.py L290-302 含三路 stop_mode 分支 |
| 9 | ToolBinding 的 state_mode/stop_mode 字段类型为 Literal | VERIFIED | Phase 33 已完成，本 phase 验证通过 |

#### Plan 03 Truths

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 10 | ToolRegistry 不再有 execute/get_tool_definitions/get_tools_by_source 方法 | VERIFIED | tool_registry.py 仅含 register/all_tools/__contains__/__len__ |
| 11 | AgentRuntimeSpec 不再有 tool_registry 字段 | VERIFIED | runtime.py 中无 tool_registry 字段定义 |
| 12 | agent.py 中无任何 spec.tool_registry 引用 | VERIFIED | grep 确认零匹配 |
| 13 | ContextBuilder._build_tools() 输出通用说明而非逐行枚举工具 | VERIFIED | 返回含 "function calling" 的静态字符串 |

**Score:** 13/13 truths verified

---

### Required Artifacts

| Artifact | Status | Details |
|----------|--------|---------|
| `matmaster/core/guard_pipeline.py` | VERIFIED | 含 `class ReadBeforeModifyGuard`，GuardPipeline 支持 read_tracker 参数 |
| `matmaster/types/guards.py` | VERIFIED | GuardContext 含 `read_tracker: Any \| None = None` 字段（L44） |
| `matmaster/core/capability_policy.py` | VERIFIED | 含 `_check_bash_safety`/`_DANGEROUS_COMMAND_PATTERNS`/`is_dangerous_bash_command` |
| `tests/matmaster/core/test_guard_pipeline.py` | VERIFIED | 含 ReadBeforeModifyGuard 测试（TestReadBeforeModifyGuard, TestGuardPipelineReadTrackerInjection），22 passed |
| `tests/matmaster/core/test_capability_policy.py` | VERIFIED | 含 bash safety 测试，17 passed |
| `matmaster/tools/tool_compiler.py` | VERIFIED | 含 BUILTIN_STOP_MODES，compile() 填充 state_mode/stop_mode |
| `matmaster/core/tool_runner.py` | VERIFIED | 含 stop_mode-aware 取消逻辑，InlineToolRunner 标注 DEPRECATED |
| `matmaster/tools/tool_registry.py` | VERIFIED | 纯存储，仅 register/all_tools/__contains__/__len__ |
| `matmaster/types/runtime.py` | VERIFIED | AgentRuntimeSpec 无 tool_registry 字段，有 read_tracker 字段 |
| `matmaster/core/context_builder.py` | VERIFIED | _build_tools() 返回通用说明，无 ToolRegistry import |
| `matmaster/tools/tool_catalog.py` | VERIFIED | 含 inject_stop_event() 和 build_definitions() |

---

### Key Link Verification

#### Plan 01 Key Links

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `guard_pipeline.py` | `types/guards.py` | GuardContext.read_tracker | VERIFIED | pipeline.evaluate() 传 read_tracker=self._read_tracker 到 ctx (L148) |
| `exp.py` | `guard_pipeline.py` | ReadTracker injection into GuardPipeline | VERIFIED | exp.py:L269 `GuardPipeline(guards, read_tracker=self._read_tracker)`；L257-259 ReadBeforeModifyGuard 加入 guards list |
| `capability_policy.py` | `bash_tool.py` | dangerous patterns 迁移后删除 | VERIFIED | bash_tool.py 无 patterns，capability_policy.py 有完整实现 |

#### Plan 02 Key Links

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `tool_compiler.py` | `types/tool_spec.py` | ToolBinding(state_mode=..., stop_mode=...) | VERIFIED | compile() L112-118 正确填充 |
| `tool_runner.py` | `types/tool_spec.py` | instance.tool_binding.stop_mode | VERIFIED | L290 `stop_mode = instance.tool_binding.stop_mode` |

#### Plan 03 Key Links

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `agent.py` | `tool_catalog.py` | spec.tool_catalog（无 spec.tool_registry） | VERIFIED | agent.py 零 tool_registry 引用，零 GuardPipeline 引用（死代码已清除） |
| `exp.py` | `tool_catalog.py` | catalog.inject_stop_event(stop_event) | VERIFIED | exp.py L354, L396 |
| `context_builder.py` | (无) | 通用 function calling 说明 | VERIFIED | _build_tools() 不依赖 tool_registry |

---

### Data-Flow Trace (Level 4)

ReadBeforeModifyGuard 数据流追踪：

| Component | Expected Flow | Actual | Status |
|-----------|--------------|--------|--------|
| ReadTracker | exp._read_tracker → GuardPipeline._read_tracker → GuardContext.read_tracker → ReadBeforeModifyGuard.evaluate() | exp.py:L269 完整注入链路，行为测试确认 unread->deny | FLOWING |
| CapabilityPolicy bash check | execute_bash → FullToolRunner → DefaultCapabilityPolicy.evaluate → _check_bash_safety | 完整链路存在 | FLOWING |
| stop_mode cancel | ToolBinding.stop_mode → FullToolRunner.execute_batch → 三路分支 | 完整链路存在 | FLOWING |
| inject_stop_event | catalog.inject_stop_event → registry.all_tools → tool._stop_event | 完整链路存在 | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Result | Status |
|----------|--------|--------|
| ReadBeforeModifyGuard via GuardPipeline(read_tracker=tracker)，unread file | allowed=False, reason="File 'test.py' must be read before modify" | PASS |
| ReadBeforeModifyGuard via GuardPipeline(read_tracker=tracker)，read file | allowed=True | PASS |
| bash_tool.py 无危险 patterns | 无 grep 匹配 | PASS |
| CapabilityPolicy 拦截危险 bash 命令 | 17 tests pass | PASS |
| ToolCompiler 填充 stop_mode | tests pass（含 stop_mode 测试） | PASS |
| FullToolRunner stop_mode-aware cancel | test_tool_runner.py tests pass | PASS |
| ToolRegistry 纯存储 | 无 execute/get_tool_definitions/get_tools_by_source | PASS |
| ContextBuilder function calling | "function calling" in output | PASS |
| agent.py 无 GuardPipeline 死代码 | grep 零匹配 | PASS |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|---------|
| CMIG-01 | 35-01 | 扩展 GuardContext 增加 ReadTracker，将 read-before-modify 从 WriteTool/EditTool 迁入 RunStateGuard | VERIFIED | exp.py:L269 完整注入修复后，ReadBeforeModifyGuard 生产路径完全生效；GuardContext.read_tracker 字段存在；行为测试通过。**REQUIREMENTS.md 仍显示 Pending — 需同步更新为 Complete** |
| CMIG-02 | 35-01 | 将 bash_tool 的 `_is_dangerous_command` 迁入 CapabilityPolicy | VERIFIED | capability_policy.py 完整实现，bash_tool.py 完全清除，17 tests pass。**REQUIREMENTS.md 仍显示 Pending — 需同步更新为 Complete** |
| CMIG-03 | 35-02 | ToolBinding state_mode/stop_mode 字段启用，Scheduler 调整策略 | VERIFIED | BUILTIN_STOP_MODES + FullToolRunner cancel-aware，REQUIREMENTS.md 标 Complete |
| CMIG-04 | 35-03 | ToolRegistry 降级为纯存储层，ToolCatalog 接管 | VERIFIED | REQUIREMENTS.md 标 Complete，代码验证通过 |
| CMIG-05 | 35-03 | ContextBuilder 工具来源迁移 | VERIFIED | REQUIREMENTS.md 标 Complete，代码验证通过 |

**Note:** REQUIREMENTS.md 中 CMIG-01 和 CMIG-02 的 checkbox（`- [ ]`）和 traceability 表（"Pending"）均未同步更新为完成状态。两项实现均已通过代码和测试验证完整。建议在下一次 REQUIREMENTS.md 维护时将两项状态同步为 Complete。

---

### Anti-Patterns Found

（re-verification 扫描）

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| `matmaster/skills/playground-skills/bohrium-job/scripts/*.py` | `from src.utils.constant import ...` | INFO | pre-existing，与 Phase 35 无关；test_import_audit 已记录此为已知问题外的"新违规"但属遗留路径 |

前次 BLOCKER 和两处 WARNING 已全部消除：

- `exp.py:L269` GuardPipeline 缺少 read_tracker 参数 — 已修复（CLOSED）
- `agent.py` 中两处 guard_pipeline 死代码 — 已清除（CLOSED）

---

### Human Verification Required

无需人工验证的功能项（所有关键验证均可程序化完成）。

---

### Test Suite Status

- `tests/matmaster/core/test_guard_pipeline.py`: 22 passed
- `tests/matmaster/core/test_capability_policy.py`: 17 passed
- `tests/matmaster/` 全套（排除 devshell + integration/real_api）: 1244 passed, 2 skipped, 1 failed（pre-existing scripts 路径导入问题，与 Phase 35 无关）

---

### Gaps Summary

**再次验证后无遗留缺口。** Phase 35 目标已完整达成：

1. 读-写前检查（read-before-modify）从 WriteTool/EditTool 中删除，edit_file 路径迁入 ReadBeforeModifyGuard（通过 GuardPipeline，read_tracker 完整注入），write_file 路径迁入 validate_input（保留 path_exists 语义）。

2. bash 危险命令检查从 BashTool 中删除，完整迁入 CapabilityPolicy。

3. ToolCompiler 填充 stop_mode/state_mode，FullToolRunner 三路 cancel 分支完整实现。

4. ToolRegistry 降级为纯存储（4 个方法），agent.py 零 tool_registry 引用，ToolCatalog 成为唯一上层消费接口。

5. ContextBuilder._build_tools() 输出通用 function calling 说明，不再依赖 ToolRegistry 枚举工具。

**遗留 action（不阻断 Phase 35）：** REQUIREMENTS.md 中 CMIG-01 和 CMIG-02 的状态仍为 Pending，需在下一次文档维护时同步为 Complete。

---

_Verified: 2026-04-03T08:00:00Z_
_Verifier: Claude (gsd-verifier)_
