---
phase: 09-tools
verified: 2026-03-25T05:00:00Z
status: passed
score: 13/13 must-haves verified
re_verification: false
---

# Phase 9: 文件操作 Tools Verification Report

**Phase Goal:** Agent 具备完整的文件读写搜索能力，并通过 Read-Before-Modify 协议防止盲写
**Verified:** 2026-03-25
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths (from ROADMAP.md Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Agent 可以通过 Read tool 读取远程文件内容（支持行范围指定） | VERIFIED | `ReadTool._execute` 调用 `session.read_file`，格式化 cat -n 输出；`line_range` 参数支持 1-indexed 切片，-1 表示读到末尾 |
| 2 | Agent 可以通过 Write tool 创建或覆盖文件、通过 Edit tool 精确字符串替换 | VERIFIED | `WriteTool` 调用 `session.write_file`；`EditTool` 使用 `re.escape` + 唯一匹配检查 + strip fallback |
| 3 | Write/Edit 执行前强制要求先 Read 目标文件，未 Read 时返回错误提示 | VERIFIED | 两个工具都检查 `self._tracker.has_been_read(posixpath.normpath(file_path))`；错误字符串精确为 `"Error: file '{path}' must be read before modify"` |
| 4 | Agent 可以通过 Glob tool 按模式搜索文件路径、通过 Grep tool 按正则搜索文件内容 | VERIFIED | `GlobTool` 包装 `find -type f -name`；`GrepTool` 包装 `grep -rn`；均通过 `_resolve_safe_path` 强制 workdir 边界 |

**Score:** 4/4 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `matmaster/tools/builtin/read_tracker.py` | ReadTracker with mark_read/has_been_read/clear | VERIFIED | 39 行，posixpath.normpath 规范化，set 内部状态 |
| `matmaster/tools/builtin/read_tool.py` | ReadTool(BuiltinTool) name="read_file" | VERIFIED | 119 行，完整 line_range 验证，tracker 注入，cat -n 格式 |
| `matmaster/tools/builtin/write_tool.py` | WriteTool(BuiltinTool) name="write_file" | VERIFIED | 69 行，RBM 协议，新文件绕过检查 |
| `matmaster/tools/builtin/edit_tool.py` | EditTool(BuiltinTool) name="edit_file" | VERIFIED | 147 行，str_replace 唯一匹配，strip fallback，上下文 snippet |
| `matmaster/tools/builtin/glob_tool.py` | GlobTool(BuiltinTool) name="glob" | VERIFIED | 82 行，_resolve_safe_path，find 命令，head -200 |
| `matmaster/tools/builtin/grep_tool.py` | GrepTool(BuiltinTool) name="grep" | VERIFIED | 91 行，_resolve_safe_path，grep -rn，--include 过滤，head -200 |
| `matmaster/tools/builtin/__init__.py` | 导出全部 6 个新符号 | VERIFIED | ReadTracker/ReadTool/WriteTool/EditTool/GlobTool/GrepTool 全部在 `__all__` 中 |
| `matmaster/core/exp.py` | _init_builtin_tools 注册 12 native + 1 evo，ReadTracker 生命周期 | VERIFIED | 265-266 行：`tracker = ReadTracker(); self._register_cleanup(tracker.clear)`；264-290 行：12 native tools + MonitorJobTool |
| `matmaster/exps/direct.toml` | 显式列举 12 个 tool 名称 | VERIFIED | builtin = ["execute_bash", ..., "task_complete"]，共 12 项，无通配符 |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `read_tool.py` | `read_tracker.py` | `self._tracker.mark_read()` in `_execute` | WIRED | 第 73-74 行，normpath + mark_read 调用 |
| `write_tool.py` | `read_tracker.py` | `self._tracker.has_been_read()` check before write | WIRED | 第 61-64 行，三重守卫条件 |
| `edit_tool.py` | `read_tracker.py` | `self._tracker.has_been_read()` check before edit | WIRED | 第 73-77 行，无 path_exists 前置检查（EditTool 要求任意文件都需先读） |
| `glob_tool.py` | `session.exec_bash` | `find` command wrapped in exec_bash | WIRED | 第 73-74 行，含 safe_path + head -200 |
| `grep_tool.py` | `session.exec_bash` | `grep -rn` command wrapped in exec_bash | WIRED | 第 81-84 行，含 include_flag + safe_path + head -200 |
| `exp.py` | `builtin/__init__.py` | `from matmaster.tools.builtin import ReadTool, ...` | WIRED | 第 248-262 行，一次性导入全部 12 个 builtin 符号 |
| `exp.py` | `read_tracker.py` | `tracker = ReadTracker(); self._register_cleanup(tracker.clear)` | WIRED | 第 265-266 行，cleanup 已注册 |
| `exp.py` (build_runtime) | `_init_builtin_tools` | `if builtin_cfg and ctx.session is not None` | WIRED | 第 113-115 行，truthiness 检查取代旧 wildcard 检查 |

---

### Data-Flow Trace (Level 4)

不适用 — 本 phase 均为工具实现层（非 UI 渲染层），数据流在单元测试中通过 mock_session 已充分覆盖。

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| 全部 phase-9 工具测试 | `uv run pytest tests/matmaster/tools/test_read_tracker.py test_read_tool.py test_write_tool.py test_edit_tool.py test_glob_tool.py test_grep_tool.py tests/matmaster/core/test_exp.py -x -q` | 97 passed | PASS |
| matmaster 完整套件无回归 | `uv run pytest tests/matmaster/ -x -q` | 771 passed | PASS |
| EditorTool 从 matmaster 中彻底移除 | `grep -r "EditorTool" matmaster/ --include="*.py"` (import/注册) | 仅 read_tool.py 文档注释中一处，无 import/注册 | PASS |
| direct.toml 无通配符 | 文件内容检查 | `builtin = [...]` 含 12 个显式名称，无 `"*"` | PASS |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| TOOL-01 | 09-01, 09-03 | Agent 通过 Read tool 读取远程文件内容（支持行范围） | SATISFIED | ReadTool 实现完整，9 个单元测试覆盖 full/line_range/open_end/not_found/tracker |
| TOOL-02 | 09-01, 09-03 | Agent 通过 Write tool 创建或覆盖远程文件 | SATISFIED | WriteTool 实现，7 个单元测试：new file/existing w/read/existing w/o read/no_tracker |
| TOOL-03 | 09-01, 09-03 | Agent 通过 Edit tool 精确字符串替换 | SATISFIED | EditTool 实现，10 个单元测试：unique/no_match/multi_match/same_strings/strip_fallback/RBM |
| TOOL-05 | 09-02, 09-03 | Agent 通过 Glob tool 按模式搜索远程文件路径 | SATISFIED | GlobTool 实现，9 个单元测试含路径安全检查 |
| TOOL-06 | 09-02, 09-03 | Agent 通过 Grep tool 按正则搜索远程文件内容 | SATISFIED | GrepTool 实现，13 个单元测试含 include 过滤 |
| TOOL-08 | 09-01, 09-03 | Write/Edit 执行前强制要求先 Read（RBM 协议） | SATISFIED | ReadTracker 6 个单元测试（含 normpath dot/dotdot）；WriteTool/EditTool 均在 _execute 首部检查 |

**6/6 requirements satisfied. 无 orphaned requirement。**

---

### Anti-Patterns Found

无。扫描 `matmaster/tools/builtin/*.py` 和 `matmaster/core/exp.py`：

- 无 TODO/FIXME/PLACEHOLDER 注释（`_init_mcp_tools` 的 stub 注释属于 Phase 9 范围外的预留）
- 无 `return null / return {} / return []` 的空实现
- 所有工具均有完整 `_execute` 逻辑，无 console.log/print 占位
- EditorTool 仅在 read_tool.py 文档注释中提及（"Replaces EditorTool._view"），非导入

---

### Human Verification Required

无自动化检查无法覆盖的项目。本 phase 全为后端工具实现，核心行为均由单元测试验证。

---

## Gaps Summary

无 gap。

---

## Summary

Phase 9 目标完全达成：

1. **5 个独立 tool 文件** — read_tool.py / write_tool.py / edit_tool.py / glob_tool.py / grep_tool.py 全部独立实现，EditorTool 已从 matmaster package 彻底移除（仅保留 evomaster/ 上游原件）
2. **Read-Before-Modify 协议** — ReadTracker 通过 posixpath.normpath 归一化路径，ReadTool 写入 tracker，WriteTool/EditTool 在 `_execute` 首部强制检查，error 字符串格式精确匹配规范
3. **Workdir 安全边界** — GlobTool/GrepTool 均内联 `_resolve_safe_path`，对 `../`、绝对路径越界均静默降级到 workdir 根
4. **Exp 集成** — 12 native tool + 1 MonitorJobTool evo adapter，ReadTracker cleanup 已注册，direct.toml 显式枚举，build_runtime condition 从 wildcard-only 变为 truthiness 检查
5. **测试覆盖** — 97 个 phase-9 相关测试通过，771 个 matmaster 全套测试无回归

---

_Verified: 2026-03-25T05:00:00Z_
_Verifier: Claude (gsd-verifier)_
