---
phase: 26-tool
verified: 2026-04-01T09:30:00Z
status: passed
score: 5/5 must-haves verified
gaps:
  - truth: "所有 matmaster 现有测试通过"
    status: resolved
    reason: "已修复：MagicMock() 改为 MagicMock(spec=Session)，TestExpBuiltinTools 11 个测试全部通过（commit 34d92c76）"
human_verification: []
---

# Phase 26: Tool 内化验证报告

**Phase Goal:** matmaster.tools 不再需要 EvoToolAdapter；builtin 工具完全原生化
**Verified:** 2026-04-01T09:30:00Z
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | bash_tool.py 原生提供 is_dangerous_bash_command（不 import evomaster） | VERIFIED | 文件无 evomaster import，`def is_dangerous_bash_command` 和 `_BLOCKED_FIRST_TOKENS` 均存在于文件内，Python 导入测试通过 |
| 2 | edit_tool.py 原生提供 SNIPPET_LINES/MAX_OUTPUT_SIZE/maybe_truncate（不 import evomaster） | VERIFIED | 文件无 evomaster import，三个符号均存在，`maybe_truncate('x'*20000)` 返回截断结果 |
| 3 | eval_tooling_snapshot.py 中 web_search 名称为 `web_search`（下划线） | VERIFIED | `_BUILTIN_WHEN_STAR` 第 37 行为 `"web_search"`，无 `"web-search"` |
| 4 | MonitorJobTool 继承 BuiltinTool，7 个文件完整，模块加载不触发 evomaster.agent.tools.builtin | VERIFIED | 7 文件均存在，`from matmaster.tools.builtin.monitor_job import MonitorJobTool` 后 sys.modules 中无 evomaster.agent.tools.builtin，schema keys 正确 |
| 5 | exp.py 原生注册，无 EvoToolAdapter / playground 依赖，所有测试通过 | PARTIAL | exp.py 原生注册已实现，adapter 文件已删除，但 TestExpBuiltinTools 13 个测试因 Phase 25-01 引入的 PlaygroundContext session 类型收紧而失败 |

**Score:** 4/5 truths verified (1 partial — 实现已完成，测试因跨 Phase 回归而失败)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `matmaster/tools/builtin/bash_tool.py` | 原生 bash safety 检查函数 | VERIFIED | 包含 `def is_dangerous_bash_command` 和 `def is_dangerous_python_content`，无 evomaster import |
| `matmaster/tools/builtin/edit_tool.py` | 原生 editor helper 常量和函数 | VERIFIED | 包含 `SNIPPET_LINES = 4`、`MAX_OUTPUT_SIZE = 16000`、`def maybe_truncate` |
| `matmaster/eval_tooling_snapshot.py` | web_search 名称快照 | VERIFIED | `_BUILTIN_WHEN_STAR` 包含 `"web_search"` |
| `matmaster/tools/builtin/monitor_job/__init__.py` | MonitorJobTool 和 run_monitor_decision_once 导出 | VERIFIED | 存在，导出两个符号 |
| `matmaster/tools/builtin/monitor_job/_tool.py` | MonitorJobTool(BuiltinTool) | VERIFIED | `class MonitorJobTool(BuiltinTool)` 存在，无 evomaster.agent import，188 行 |
| `matmaster/tools/builtin/monitor_job/_constants.py` | TERMINAL_SUCCESS 等常量，REPO_ROOT parents[4] | VERIFIED | `parents[4]` 和 `TERMINAL_SUCCESS` 均存在，84 行 |
| `matmaster/tools/builtin/monitor_job/_lifecycle.py` | 核心轮询循环，evomaster.adaptors 为 lazy import | VERIFIED | `_run_lifecycle` 存在，evomaster.adaptors.calculation 在函数内 lazy import，446 行 |
| `matmaster/tools/builtin/monitor_job/_download.py` | 结果下载逻辑，lazy import | VERIFIED | `_download_results_to_local_dir` 存在，lazy import 已到位，238 行 |
| `matmaster/tools/builtin/monitor_job/_llm.py` | LLM 决策，lazy import | VERIFIED | 存在，lazy import，220 行 |
| `matmaster/tools/builtin/monitor_job/_logs.py` | 日志与 run_monitor_decision_once，lazy import | VERIFIED | `run_monitor_decision_once` 存在，lazy import，300 行 |
| `matmaster/core/exp.py` | 原生 tool 注册（无 EvoToolAdapter） | VERIFIED | 包含 `from matmaster.tools.builtin.monitor_job import MonitorJobTool`，`MonitorJobTool(session=ctx.session, workdir=exec_wd)`，无 EvoToolAdapter |
| `matmaster/tools/__init__.py` | clean 导出（仅 Tool, ToolRegistry） | VERIFIED | `__all__ = ["Tool", "ToolRegistry"]`，无 EvoToolAdapter |
| `matmaster/tools/evomaster_tool_adapter.py` | 文件已删除 | VERIFIED | `ls` 返回 "No such file" |
| `tests/matmaster/tools/test_evomaster_tool_adapter.py` | 测试文件已删除 | VERIFIED | `ls` 返回 "No such file" |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `bash_tool.py` | `is_dangerous_bash_command` (inlined) | module-level def | WIRED | 第 65 行定义，BashTool._execute 调用 |
| `edit_tool.py` | `maybe_truncate` (inlined) | module-level def | WIRED | 第 30 行定义，EditTool._execute 调用 |
| `monitor_job/_tool.py` | `matmaster/tools/builtin/base.py` | class inheritance | WIRED | `class MonitorJobTool(BuiltinTool)` |
| `monitor_job/_tool.py` | `matmaster/tools/tool_result.py` | return type | WIRED | `return ToolResult(status=..., content=...)` |
| `monitor_job/_tool.py` | `_lifecycle._run_lifecycle` | function call in _execute | WIRED | `from ._lifecycle import _run_lifecycle`，在 `_execute` 内调用 |
| `exp.py` | `matmaster.tools.builtin.monitor_job` | direct import + register | WIRED | 第 393 行 `from matmaster.tools.builtin.monitor_job import MonitorJobTool`，第 401 行 `registry.register(tool, source='builtin')` |

### Data-Flow Trace (Level 4)

Level 4 check 不适用于本 Phase 核心交付物（工具注册基础设施，非数据渲染组件）。MonitorJobTool._execute 依赖 evomaster.adaptors.calculation.job_service 提供真实数据，属于 lazy import 覆盖的外部运行时依赖。

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| bash safety 检测危险命令 | `python -c "from matmaster.tools.builtin.bash_tool import is_dangerous_bash_command; r=is_dangerous_bash_command('rm -rf /'); assert r[0] is True"` | (True, '...destructive...') | PASS |
| edit helper 截断函数 | `python -c "from matmaster.tools.builtin.edit_tool import maybe_truncate,MAX_OUTPUT_SIZE; assert MAX_OUTPUT_SIZE==16000; r=maybe_truncate('x'*20000); assert len(r)<20000"` | ok | PASS |
| MonitorJobTool 加载不触发 evomaster.agent.tools.builtin | `python -c "import sys; import matmaster.tools.builtin.monitor_job; bad=[k for k in sys.modules if 'evomaster.agent.tools.builtin' in k]; assert not bad"` | CLEAN | PASS |
| MonitorJobTool schema 正确 | `python -c "from matmaster.tools.builtin.monitor_job import MonitorJobTool; t=MonitorJobTool(session=None); assert t.name=='monitor_job'; assert 'job_id' in t.json_schema['properties']"` | name=monitor_job, 14 keys | PASS |
| exp.py 无 playground.mat_master.tools 加载 | `python -c "import sys; from matmaster.core.exp import Exp; bad=[k for k in sys.modules if 'playground.mat_master.tools' in k]; assert not bad"` | CLEAN | PASS |
| exp.py 无 EvoToolAdapter 引用 | `grep -rn EvoToolAdapter matmaster/` | 0 matches | PASS |

**注意 — exp.py 加载时 evomaster.agent.tools.builtin 仍被加载**

`from matmaster.core.exp import Exp` 后 `sys.modules` 中会出现 `evomaster.agent.tools.builtin.*`，但根因是 `matmaster/core/playground.py`（第 26-29 行）的 evomaster 导入链，而非 Phase 26 修改的代码。调用链：

```
matmaster.types.session → matmaster.types.context → matmaster.types.__init__
  → matmaster.core.playground (via hooks → types → session chain)
    → evomaster.agent.session.base (Phase 25 scope)
      → evomaster/__init__.py (from evomaster.agent import ...)
        → evomaster.agent.tools.builtin.* (全量加载)
```

这是 Phase 25（PLAY-02/PLAY-03）的待解耦依赖，在 26-02-SUMMARY 中已记录为"pre-existing condition"。

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|---------|
| TOOL-07 | 26-03 | matmaster.tools 注册遗留 builtin 能力不需要 EvoToolAdapter | SATISFIED | EvoToolAdapter 文件已删除，exp.py 使用 `registry.register(tool, source='builtin')`，matmaster/ 包内无 EvoToolAdapter 运行时引用 |
| TOOL-08 | 26-01 | matmaster.tools.builtin 使用原生 bash safety 与 edit helper | SATISFIED | bash_tool.py 和 edit_tool.py 内联所有 helper，无 evomaster.agent.tools.builtin import |
| TOOL-09 | 26-02 | MonitorJobTool 通过 matmaster 原生注册，exp.py 不 lazy import evomaster.agent.tools.builtin.monitor_job | SATISFIED | matmaster/tools/builtin/monitor_job/ 7 文件存在，exp.py 第 393-402 行使用原生注册 |
| TOOL-10 | 26-01, 26-03 | web_search_tool 原生实现，exp.py 不 import playground.mat_master.tools.web_search | SATISFIED | eval_tooling_snapshot.py 使用 `"web_search"`，exp.py 无 playground.mat_master import，WebSearchTool 已在 native_tools 列表 |

**注意：** REQUIREMENTS.md 中 TOOL-07、TOOL-08、TOOL-10 仍标记为 `[ ]`（未更新），但实现已完成。TOOL-09 已正确标为 `[x]`。需要手动更新 REQUIREMENTS.md 中三个条目的状态。

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `matmaster/tools/builtin/edit_tool.py` | 41 | `from .read_tracker import ReadTracker` 紧接在 `# ---- End Editor helpers ----` 注释后，没有空行分隔 | Info | 无功能影响，仅代码风格问题 |

无 STUB、PLACEHOLDER、空实现或硬编码空数据反模式。

### Human Verification Required

#### 1. TestExpBuiltinTools 回归修复验证

**测试：** 将 `tests/matmaster/core/test_exp.py` 中 `TestExpBuiltinTools._make_ctx_with_session` 的 `MagicMock()` 替换为满足 `Session` Protocol 的 mock（例如使用 `create_autospec` 或实现 Protocol 必要方法），然后运行 `uv run pytest tests/matmaster/core/test_exp.py -x -q`

**期望：** 所有 TestExpBuiltinTools 和 TestExecutionWorkdirBinding 测试通过

**Why human:** 修复需要修改测试代码，属于 gap 修复范畴，超出自动化验证范围

---

## Gaps Summary

**Gap 1: TestExpBuiltinTools 13 个测试失败（跨 Phase 回归）**

Phase 26 的实现代码完全正确。失败原因：Phase 26-03（commit `40bf1ce9`）在 `test_exp.py` 中编写了 `TestExpBuiltinTools` 类，使用 `MagicMock()` 作为 `PlaygroundContext.session`。随后 Phase 25-01（commit `3476fa9c`，时间戳更晚）把 `PlaygroundContext.session` 从 `Any` 改为 `Session | None`，pydantic 严格校验导致 `MagicMock()` 被拒绝。

**根因分类：** 这是 Phase 25 的改动破坏了 Phase 26 的测试，不是 Phase 26 实现逻辑的问题。

**修复方案：** 在 `_make_ctx_with_session` 中构造满足 `Session` Protocol 的 mock 对象。最简单方式：

```python
from unittest.mock import MagicMock, create_autospec
from matmaster.types.session import Session

def _make_ctx_with_session(self, tmp_path: Path) -> PlaygroundContext:
    mock_session = create_autospec(Session, instance=True)
    return PlaygroundContext(
        workdir=tmp_path,
        session_type='local',
        cache_area=tmp_path / 'cache',
        session=mock_session,
        llm_provider=MockLLMProvider(),
    )
```

**Phase 26 核心目标（matmaster.tools 不再需要 EvoToolAdapter；builtin 工具完全原生化）已完全实现。** 唯一 gap 是跨 Phase 的测试兼容性问题，需要 1 处测试修复。

---

_Verified: 2026-04-01T09:30:00Z_
_Verifier: Claude (gsd-verifier)_
