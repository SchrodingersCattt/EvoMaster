---
phase: 8
slug: builtintool-tools
status: complete
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-25
audited: 2026-03-25
---

# Phase 8 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (via `uv run pytest`) |
| **Config file** | `pytest.ini` (root) |
| **Quick run command** | `uv run pytest tests/matmaster/tools/ -x` |
| **Full suite command** | `uv run pytest tests/ -x` |
| **Estimated runtime** | ~0.3 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/tools/ -x`
- **After every plan wave:** Run `uv run pytest tests/ -x`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 08-01-01 | 01 | 1 | ALL | unit | `uv run pytest tests/matmaster/tools/test_builtin_base.py -x` | ✅ | ✅ green (7 tests) |
| 08-02-01 | 02 | 1 | TOOL-04 | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py -x` | ✅ | ✅ green (7 tests) |
| 08-02-02 | 02 | 1 | TOOL-04 | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py::TestBashToolExecution::test_dangerous_command_blocked -x` | ✅ | ✅ green |
| 08-02-03 | 02 | 1 | TOOL-07 | unit | `uv run pytest tests/matmaster/tools/test_listdir_tool.py -x` | ✅ | ✅ green (6 tests) |
| 08-03-01 | 03 | 1 | TOOL-09 | unit | `uv run pytest tests/matmaster/tools/test_task_tools.py -k "TaskCreateTool" -x` | ✅ | ✅ green (3 tests) |
| 08-03-02 | 03 | 1 | TOOL-09 | unit | `uv run pytest tests/matmaster/tools/test_task_tools.py -k "TaskGetTool" -x` | ✅ | ✅ green (3 tests) |
| 08-03-03 | 03 | 1 | TOOL-09 | unit | `uv run pytest tests/matmaster/tools/test_task_tools.py -k "TaskListTool" -x` | ✅ | ✅ green (3 tests) |
| 08-03-04 | 03 | 1 | TOOL-09 | unit | `uv run pytest tests/matmaster/tools/test_task_tools.py -k "TaskUpdateTool" -x` | ✅ | ✅ green (3 tests) |
| 08-03-05 | 03 | 1 | TOOL-09 | unit | `uv run pytest tests/matmaster/tools/test_task_tools.py -k "TaskCompleteTool" -x` | ✅ | ✅ green (3 tests) |
| 08-04-01 | 03 | 2 | ALL | integration | `uv run pytest tests/matmaster/core/test_exp.py -k "builtin" -x` | ✅ | ✅ green (5 tests) |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

**Total: 62 tests, 62 passed, 0 failed**

---

## Wave 0 Requirements

- [x] `tests/matmaster/tools/test_builtin_base.py` — BuiltinTool ABC 基类测试（Protocol 满足、_require_session、错误处理）
- [x] `tests/matmaster/tools/test_bash_tool.py` — BashTool 单元测试（执行、危险命令拦截）
- [x] `tests/matmaster/tools/test_listdir_tool.py` — ListDirTool 单元测试
- [x] `tests/matmaster/tools/test_task_tools.py` — 5 个 TaskTool 单元测试（37 tests covering TaskStore + 5 tools）
- [x] `tests/matmaster/tools/conftest.py` — 共享 fixtures（mock session、临时 workdir）

*`tests/matmaster/core/test_exp.py` covers Exp integration with 5 builtin tool registration tests.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 10s (actual: ~0.3s)
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** PASSED

---

## Validation Audit 2026-03-25

| Metric | Count |
|--------|-------|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |
| Total tests | 62 |
| Tests passing | 62 |
