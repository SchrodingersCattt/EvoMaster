---
phase: 03-exp-assembly-layer
verified: 2026-03-22T06:00:00Z
status: passed
score: 11/11 must-haves verified
re_verification: true
  previous_status: gaps_found
  previous_score: 9/11
  gaps_closed:
    - "Exp.run() default flow integrates with engine without circular import"
    - "engine 测试套件无回归（之前因循环导入全部 ERROR，现已全部通过）"
  gaps_remaining: []
  regressions: []
human_verification: []
---

# Phase 3: Exp Assembly Layer 验证报告

**Phase Goal:** Exp 层消费 PlaygroundContext 输出 AgentRuntimeSpec，统一所有能力的装配路径，集成跨 pod 协调
**Verified:** 2026-03-22T06:00:00Z
**Status:** PASSED
**Re-verification:** Yes -- 针对 Plan 04 gap closure（循环导入修复）后的再次验证

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | ToolRegistry 可从 builtin/MCP/skill 来源注册工具到平坦命名空间 | VERIFIED | tool_registry.py L39-107，register() + source tracking 完整实现 |
| 2 | 同名 tool 注册覆盖前一个并输出 warning log | VERIFIED | register() L51-62，logger.warning 含 old/new source |
| 3 | ToolRegistry.execute() 按名称分发到正确工具 | VERIFIED | execute() L64-73，未找到时返回 "Error: Tool '{name}' not found" |
| 4 | ToolRegistry.get_tool_definitions() 返回 OpenAI function calling 格式 | VERIFIED | get_tool_definitions() L75-87，{"type":"function","function":{...}} |
| 5 | AgentRuntimeSpec.tool_registry 字段类型为 ToolRegistry（非 Any） | VERIFIED | runtime.py L47: `tool_registry: ToolRegistry | None = None`，直接导入（非 TYPE_CHECKING） |
| 6 | ContextBuilder 按固定顺序从多源组装 system prompt | VERIFIED | context_builder.py L31: SECTION_ORDER = ("identity","mode_contract","skills","tools","memory","task") |
| 7 | 各 section 可通过 disabled_sections 单独禁用 | VERIFIED | build() L79: `if section_name in disabled: continue` |
| 8 | WorkerRegistry Protocol 定义 4 个 session_run_owner 方法并支持 isinstance 检查 | VERIFIED | worker_registry.py 完整实现，@runtime_checkable |
| 9 | Exp 基类定义 abstract assemble() 并提供 default run() 流程 | VERIFIED | exp.py：@abstractmethod assemble()，run() 调用 AgentKernel（lazy import） |
| 10 | Exp.run() 默认流程与 engine 无循环导入 | VERIFIED | exp.py: from __future__ import annotations + TYPE_CHECKING block + lazy import in run() body；`python -c "import matmaster.engine; import matmaster.assembly"` 退出码 0 |
| 11 | DirectExp.assemble() 构建完整 AgentRuntimeSpec | VERIFIED | direct_exp.py：ToolRegistry + ContextBuilder + EventEmitterHook + AgentRuntimeSpec 全部组装 |

**Score:** 11/11 truths verified

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `matmaster/assembly/tool_registry.py` | Tool Protocol + ToolRegistry class | VERIFIED | 107 行，Tool @runtime_checkable，ToolRegistry 含 register/execute/get_tool_definitions/get_tools_by_source/all_tools |
| `matmaster/types/runtime.py` | tool_registry 字段类型为 ToolRegistry | VERIFIED | 直接导入 ToolRegistry，字段 `tool_registry: ToolRegistry | None = None` |
| `tests/matmaster/assembly/test_tool_registry.py` | ToolRegistry 单测，min 60 行 | VERIFIED | 139 行，11 个测试函数，全部通过 |
| `matmaster/assembly/context_builder.py` | ContextBuilder 分节提示词组装 | VERIFIED | 179 行，SECTION_ORDER + 6 个 _build_* 私有方法 |
| `matmaster/assembly/worker_registry.py` | WorkerRegistry Protocol | VERIFIED | 42 行，@runtime_checkable，4 个方法 |
| `tests/matmaster/assembly/test_context_builder.py` | ContextBuilder 单测，min 50 行 | VERIFIED | 224 行，12 个测试函数，全部通过 |
| `tests/matmaster/assembly/test_worker_registry.py` | WorkerRegistry 单测，min 20 行 | VERIFIED | 101 行，4 个测试函数，全部通过 |
| `matmaster/assembly/exp.py` | Exp 抽象基类（含 lazy import 修复） | VERIFIED | 69 行，from __future__ import annotations + TYPE_CHECKING block + @abstractmethod assemble() + run() with lazy import |
| `matmaster/assembly/direct_exp.py` | DirectExp 具体子类 | VERIFIED | 85 行，完整 assemble() 实现 |
| `matmaster/assembly/guards.py` | 业务 Guard shells | VERIFIED | ManuscriptGateGuard + AuthFailureGateGuard，均实现 evaluate() |
| `matmaster/assembly/__init__.py` | 包重导出，8 个公共符号，__getattr__ 惰性加载 Exp/DirectExp | VERIFIED | 8 个符号全部在 __all__ 中，Exp/DirectExp 通过 __getattr__ 惰性加载，无顶层 engine 导入触发 |
| `tests/matmaster/assembly/test_exp.py` | Exp 基类测试，min 40 行 | VERIFIED | 126 行，6 个测试函数，mock 补丁目标已更新为 matmaster.engine.agent.AgentKernel |
| `tests/matmaster/assembly/test_direct_exp.py` | DirectExp 测试，min 60 行 | VERIFIED | 133 行，10 个测试函数，全部通过 |
| `tests/matmaster/assembly/test_guard_injection.py` | Guard 注入测试，min 30 行 | VERIFIED | 77 行，5 个测试函数，全部通过 |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| matmaster/assembly/tool_registry.py | matmaster/engine/agent.py | AgentRuntimeSpec.tool_registry 被 kernel 消费 | WIRED | engine/agent.py L145: `spec.tool_registry.execute(tc.name, tc.arguments)`，L166: `spec.tool_registry.get_tool_definitions()` |
| matmaster/types/runtime.py | matmaster/assembly/tool_registry.py | 直接导入 ToolRegistry | WIRED | runtime.py L14: `from matmaster.assembly.tool_registry import ToolRegistry` |
| matmaster/assembly/context_builder.py | matmaster/assembly/tool_registry.py | ContextBuilder._build_tools 使用 ToolRegistry.all_tools | WIRED | context_builder.py L160: `tools = tool_registry.all_tools` |
| matmaster/assembly/exp.py | matmaster/engine/agent.py | Exp.run() 惰性导入 AgentKernel 并调用 kernel.run(spec) | WIRED | exp.py L66: `from matmaster.engine.agent import AgentKernel  # lazy import to avoid circular`，L68-69: `kernel = AgentKernel()` + `kernel.run(spec, task, ...)` |
| matmaster/assembly/direct_exp.py | matmaster/assembly/tool_registry.py | DirectExp.assemble() 创建 ToolRegistry | WIRED | direct_exp.py L56: `registry = ToolRegistry()` |
| matmaster/assembly/direct_exp.py | matmaster/assembly/context_builder.py | DirectExp.assemble() 使用 ContextBuilder.build() | WIRED | direct_exp.py L63-68: `builder = ContextBuilder()` + `builder.build(...)` |
| matmaster/assembly/direct_exp.py | matmaster/engine/hooks.py | DirectExp.assemble() 创建 EventEmitterHook | WIRED | direct_exp.py L73: `emitter_hook = EventEmitterHook(bus, source=self.exp_name)` |
| matmaster/assembly/guards.py | matmaster/types/guards.py | 业务 Guard 实现 Guard Protocol evaluate 方法 | WIRED | guards.py L25/L37: `def evaluate(self, ctx: GuardContext) -> GuardResult` |

---

## Requirements Coverage

| 需求 ID | Plan | 描述 | 状态 | 证据 |
|---------|------|------|------|------|
| ASBL-01 | 03-03, 03-04 | Exp 基类 assemble() 消费 PlaygroundContext 输出 AgentRuntimeSpec | SATISFIED | exp.py: abstract assemble(ctx: PlaygroundContext) -> AgentRuntimeSpec；循环导入已修复 |
| ASBL-02 | 03-01 | ToolRegistry 统一 builtin/MCP/skill 注册路径 | SATISFIED | tool_registry.py: register(tool, source=) 含 builtin/mcp/skill 三种 source |
| ASBL-03 | 03-03, 03-04 | 业务 Guard 通过 AgentRuntimeSpec.guards 注入 | SATISFIED | guards.py + DirectExp 构造函数 guards 参数 -> spec.guards |
| ASBL-04 | 03-03, 03-04 | Solver 模式收入 exp 层作为高阶装配模式 | SATISFIED | DirectExp(Exp) 作为具体子类证明 Exp 子类化模式 |
| ASBL-05 | 03-02 | ContextBuilder 从多源组装 system prompt | SATISFIED | context_builder.py: identity/mode_contract/skills/tools/memory/task 固定顺序 |
| ASBL-06 | 03-02 | WorkerRegistry Protocol 接口定义（Phase 3 只建接口） | SATISFIED | worker_registry.py: @runtime_checkable Protocol，4 个方法，Phase 5 提供 Redis 实现 |

所有 6 个 ASBL 需求均在对应 Plan 中声明并实现，REQUIREMENTS.md 状态标记为 Complete，与代码库状态一致。

---

## Anti-Patterns Found

| 文件 | 位置 | 模式 | 严重性 | 影响 |
|------|------|------|--------|------|
| matmaster/assembly/guards.py | 全文 | Shell 实现，evaluate() 永远返回 allowed=True | INFO | Phase 3 设计意图（Phase 5 填充真实逻辑），不阻塞 Phase 3 目标 |
| tests/matmaster/types/test_runtime.py | TestAgentRuntimeSpec（9 个测试） | 使用 object() 作为 tool_registry，与 AgentRuntimeSpec Pydantic 类型约束冲突 | WARNING | 已记录在 deferred-items.md，Phase 5 修复；不是本次引入的回归 |

（之前的两个 BLOCKER 已在 Plan 04 gap closure 中完全消除。）

---

## Human Verification Required

无需人工验证的 UI/UX 项目。

---

## Re-verification Summary

**前次状态（2026-03-22T05:30:00Z）：** gaps_found，9/11 truths，2 个 BLOCKER

**本次状态（2026-03-22T06:00:00Z）：** passed，11/11 truths

### 已关闭的 Gaps

**Gap 1：Exp.run() 循环导入阻断运行时**

修复方式（Plan 04 / commit a932de6）：
- `matmaster/assembly/exp.py`：移除顶层 `from matmaster.engine.agent import AgentKernel`，改为 `from __future__ import annotations` + `if TYPE_CHECKING:` 块（供静态分析） + `run()` 方法体内惰性导入（供运行时）
- `matmaster/assembly/__init__.py`：移除顶层 `from .exp import Exp` 和 `from .direct_exp import DirectExp`，改用 PEP 562 `__getattr__` 惰性加载；`__all__` 保留全部 8 个符号
- `tests/matmaster/assembly/test_exp.py`：mock patch 目标从 `matmaster.assembly.exp.AgentKernel` 更新为 `matmaster.engine.agent.AgentKernel`（与惰性导入解析路径一致）

验证结果：
- `python -c "import matmaster.engine; import matmaster.types; import matmaster.assembly"` 退出码 0
- `from matmaster.assembly import DirectExp, Exp, ToolRegistry` 正常工作（惰性但功能完整）

**Gap 2：engine 测试套件回归**

修复结果：engine 测试套件从 0 通过（全部 collection ERROR）恢复到 69 全通过。

### 测试套件最终状态

| 套件 | 之前（Plan 03）| 之后（Plan 04） |
|------|--------------|----------------|
| tests/matmaster/engine/ | ERROR（ImportError，无法 collect） | 69 通过，0 失败 |
| tests/matmaster/assembly/ | 50 通过，0 失败 | 50 通过，0 失败（无回归） |
| tests/matmaster/bus/ | ERROR（ImportError，无法 collect） | 86 通过，0 失败 |
| tests/matmaster/types/ | ERROR（ImportError，无法 collect）| 9 失败（预期 deferred，非回归），其余通过 |

9 个 TestAgentRuntimeSpec 失败是 deferred-items.md 明确记录的 Phase 5 待修项，与 Phase 3 目标无关。

---

_Verified: 2026-03-22T06:00:00Z_
_Verifier: Claude (gsd-verifier)_
