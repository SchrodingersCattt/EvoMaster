---
phase: 08-builtintool-tools
verified: 2026-03-25T00:00:00Z
status: passed
score: 11/11 must-haves verified
re_verification: false
gaps: []
human_verification: []
---

# Phase 8: BuiltinTool Tools Verification Report

**Phase Goal:** Agent 可以通过原生 BuiltinTool 体系执行 shell 命令、浏览目录和追踪任务
**Verified:** 2026-03-25
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #  | Truth | Status | Evidence |
|----|-------|--------|----------|
| 1  | BuiltinTool 子类实例通过 isinstance(tool, Tool) Protocol 检查 | VERIFIED | 7 个 native tool 全部通过 Protocol runtime check，`uv run python -c "isinstance(t, Tool)"` 逐一确认 |
| 2  | BashTool 通过 session.exec_bash 执行命令并返回输出 | VERIFIED | `bash_tool.py:74-90` 调用 `session.exec_bash` 并拼接 output+working_dir+exit_code |
| 3  | BashTool 拦截危险命令并返回 Blocked 提示 | VERIFIED | `bash_tool.py:66-68` 调用 `is_dangerous_bash_command`，命中时 `return f"Blocked: {reason}"` |
| 4  | ListDirTool 通过 session.exec_bash 列出目录并返回输出 | VERIFIED | `listdir_tool.py:35-47` exec_bash `ls -la "{path}"`，exit_code != 0 时返回 Error 前缀 |
| 5  | session 未注入时 _require_session 抛出 RuntimeError | VERIFIED | `base.py:58-64` 检查 `self._session is None` 并 raise RuntimeError |
| 6  | TaskCreateTool 创建任务并返回包含 id/description/status/created_at 的 JSON | VERIFIED | `task_create.py:28-33` 检查 workdir，调用 `store.create`，返回 `json.dumps(task)` |
| 7  | 任务持久化到 workdir/.tasks.json 文件 | VERIFIED | `_store.py:36` `self._path = workdir / ".tasks.json"`，测试 `test_persistence_across_instances` 通过 |
| 8  | workdir 为 None 时所有 TaskTool 返回友好错误而非 traceback | VERIFIED | 各 TaskTool `_execute` 首行检查 `self._workdir is None`，返回 `"Error: workdir not available for task tracking"` |
| 9  | Exp._init_builtin_tools 注册 7 个 native builtin tool（source='builtin'） | VERIFIED | `exp.py:249-259` 构造 7 个 native tool 并逐一 `registry.register(tool, source="builtin")`；集成测试 `test_init_builtin_tools_registers_native_tools` 断言 `len==7` 通过 |
| 10 | Exp._init_builtin_tools 保留 MonitorJobTool 和 EditorTool 走 EvoToolAdapter（source='builtin_evo'） | VERIFIED | `exp.py:264-269` import EditorTool/MonitorJobTool 并 `registry.register(adapted, source="builtin_evo")`；集成测试 `test_init_builtin_tools_registers_evo_adapter_tools` 断言 `len==2` 通过 |
| 11 | build_runtime 后 registry 包含 execute_bash、list_dir 及 5 个 task tool 共 7 个 native tool | VERIFIED | `test_init_builtin_tools_native_tool_names` 验证所有 7 个 name；Protocol 脚本输出 `['execute_bash', 'list_dir', 'task_create', 'task_get', 'task_list', 'task_update', 'task_complete']` |

**Score:** 11/11 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `matmaster/tools/builtin/base.py` | BuiltinTool ABC 基类 | VERIFIED | `class BuiltinTool(ABC):`，含 `_require_session`、`execute` 模板方法、`_execute` 抽象方法 |
| `matmaster/tools/builtin/bash_tool.py` | BashTool 实现 | VERIFIED | `class BashTool(BuiltinTool):`，`name = "execute_bash"`，危险命令拦截逻辑完整 |
| `matmaster/tools/builtin/listdir_tool.py` | ListDirTool 实现 | VERIFIED | `class ListDirTool(BuiltinTool):`，`name = "list_dir"` |
| `matmaster/tools/builtin/__init__.py` | 导出所有 8 个类 | VERIFIED | 导出 BuiltinTool + BashTool + ListDirTool + 5x TaskTool，`__all__` 齐全 |
| `matmaster/tools/builtin/task/_store.py` | TaskStore 读写 .tasks.json | VERIFIED | `class TaskStore`，`_lock = threading.Lock()`，`self._path = workdir / ".tasks.json"` |
| `matmaster/tools/builtin/task/task_create.py` | TaskCreateTool | VERIFIED | `class TaskCreateTool(BuiltinTool):`，`name = "task_create"` |
| `matmaster/tools/builtin/task/task_get.py` | TaskGetTool | VERIFIED | `class TaskGetTool(BuiltinTool):`，`name = "task_get"` |
| `matmaster/tools/builtin/task/task_list.py` | TaskListTool | VERIFIED | `class TaskListTool(BuiltinTool):`，`name = "task_list"` |
| `matmaster/tools/builtin/task/task_update.py` | TaskUpdateTool | VERIFIED | `class TaskUpdateTool(BuiltinTool):`，`name = "task_update"` |
| `matmaster/tools/builtin/task/task_complete.py` | TaskCompleteTool | VERIFIED | `class TaskCompleteTool(BuiltinTool):`，`name = "task_complete"` |
| `matmaster/core/exp.py` | 改造后的 _init_builtin_tools 双源注册 | VERIFIED | `source="builtin"` 和 `source="builtin_evo"` 均存在，旧的 evo BashTool import 已移除 |
| `tests/matmaster/tools/test_builtin_base.py` | BuiltinTool 基类测试 | VERIFIED | 含 Protocol isinstance 断言、_require_session、execute 异常处理 |
| `tests/matmaster/tools/test_bash_tool.py` | BashTool 测试 | VERIFIED | 含 `test_dangerous_command_blocked`，全部通过 |
| `tests/matmaster/tools/test_listdir_tool.py` | ListDirTool 测试 | VERIFIED | 正常列目录、错误处理、默认路径全部通过 |
| `tests/matmaster/tools/test_task_tools.py` | TaskStore + 5 个 TaskTool 测试 | VERIFIED | 27 个测试全部通过，含持久化验证 |
| `tests/matmaster/core/test_exp.py` | Exp 双源注册集成测试 | VERIFIED | 5 个 builtin 测试全部通过：native 7 个、evo 2 个、total 9 个、no_session 跳过 |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `matmaster/tools/builtin/bash_tool.py` | `evomaster/agent/tools/builtin/bash_safety.py` | `from evomaster.agent.tools.builtin.bash_safety import is_dangerous_bash_command` | WIRED | Line 13，调用 `is_dangerous_bash_command(command)` at line 66 |
| `matmaster/tools/builtin/base.py` | `matmaster/tools/tool_registry.py` | satisfies Tool Protocol | WIRED | 7 个子类全部通过 `isinstance(t, Tool)` runtime Protocol check |
| `matmaster/tools/builtin/task/task_create.py` | `matmaster/tools/builtin/task/_store.py` | `TaskStore(self._workdir)` | WIRED | Line 9 import，line 31 `store = TaskStore(self._workdir)` |
| `matmaster/tools/builtin/task/_store.py` | `workdir/.tasks.json` | file read/write | WIRED | Line 36 `self._path = workdir / ".tasks.json"`，`_read`/`_write` 方法都使用该路径 |
| `matmaster/core/exp.py` | `matmaster/tools/builtin/__init__.py` | `from matmaster.tools.builtin import` | WIRED | `exp.py:239-247` 延迟 import 全部 7 个 native tool |
| `matmaster/core/exp.py` | `matmaster/tools/tool_registry.py` | `registry.register(tool, source='builtin')` | WIRED | `exp.py:259` native 注册，`exp.py:269` evo adapter 注册 |

---

### Data-Flow Trace (Level 4)

不适用——Phase 8 的产出是后端 Python 工具库，无 React/前端渲染组件，不需要 Level 4 数据流追踪。

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| 所有 native tool 满足 Tool Protocol | `uv run python -c "from matmaster.tools.tool_registry import Tool; from matmaster.tools.builtin import *; [assert isinstance(t, Tool) for t in [...]]"` | ALL Protocol checks pass | PASS |
| 7 个 tool name 正确 | 同上，打印 `[t.name for t in tools]` | `['execute_bash', 'list_dir', 'task_create', 'task_get', 'task_list', 'task_update', 'task_complete']` | PASS |
| tool 单元测试全部通过 | `uv run pytest tests/matmaster/tools/test_builtin_base.py tests/matmaster/tools/test_bash_tool.py tests/matmaster/tools/test_listdir_tool.py tests/matmaster/tools/test_task_tools.py -x -v` | 57 passed | PASS |
| Exp 双源注册集成测试通过 | `uv run pytest tests/matmaster/core/test_exp.py -x -k "builtin"` | 5 passed | PASS |
| matmaster 全量测试无回归 | `uv run pytest tests/matmaster/ -x -q` | 659 passed, 0 failures | PASS |

**备注：** `tests/test_streaming_thought_protocol.py` 收集失败（ImportError: `_should_persist_event` 不存在于 `src.services.agent_run_service`）。该错误与 Phase 8 无关，为预存在缺陷，不影响本次验证。

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| TOOL-04 | 08-01, 08-03 | Agent 可以通过 Bash tool 在远程环境执行 shell 命令 | SATISFIED | BashTool 实现完整，通过 session.exec_bash 执行，集成到 Exp，REQUIREMENTS.md 标记为 [x] |
| TOOL-07 | 08-01, 08-03 | Agent 可以通过 ListDir tool 列出远程目录结构 | SATISFIED | ListDirTool 实现完整，通过 session.exec_bash 执行 ls -la，集成到 Exp，REQUIREMENTS.md 标记为 [x] |
| TOOL-09 | 08-02, 08-03 | Agent 可以通过 Task 套件创建、更新、查询任务状态用于工作追踪 | SATISFIED | 5 个 TaskTool + TaskStore 实现完整，持久化到 .tasks.json，集成到 Exp，REQUIREMENTS.md 标记为 [x] |

**孤儿需求检查：** REQUIREMENTS.md Traceability 表中映射到 Phase 8 的需求为 TOOL-04、TOOL-07、TOOL-09，与三个 PLAN 文件中声明的需求集合完全一致，无孤儿需求。

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `matmaster/core/exp.py` | 282 | `_init_skill_tools` 方法注释含 "stub" | Info | 与 Phase 8 无关，为 Phase 未来任务预留，不影响本 Phase 目标 |

无 Blocker 或 Warning 级别的反模式。所有检查的工具实现均有真实逻辑，无空返回、无 TODO 占位、无 hardcoded 空数据。

---

### Human Verification Required

无。所有目标行为均可通过单元测试和集成测试程序验证，无需人工测试。

---

### Gaps Summary

无 Gap。Phase 8 的所有目标均已达成：

- BuiltinTool ABC 基类建立，满足 Tool Protocol，提供 `_require_session`/`execute` 模板方法
- BashTool 实现完整：命令执行、危险命令拦截、proxy clear 前缀、输出格式化
- ListDirTool 实现完整：通过 session.exec_bash 执行 ls -la，错误处理正确
- TaskStore + 5 个 TaskTool 实现完整：CRUD、持久化到 .tasks.json、workdir=None 友好处理
- Exp._init_builtin_tools 双源注册：7 个 native tool（source="builtin"）+ 2 个 evo adapter（source="builtin_evo"）
- 旧的 evomaster BashTool 依赖已正确移除，新 native BashTool 接管
- 全量测试（659 个）无回归

---

_Verified: 2026-03-25_
_Verifier: Claude (gsd-verifier)_
