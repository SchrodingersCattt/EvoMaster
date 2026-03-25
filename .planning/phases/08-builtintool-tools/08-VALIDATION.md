---
phase: 8
slug: builtintool-tools
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-25
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
| **Estimated runtime** | ~10 seconds |

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
| 08-01-01 | 01 | 1 | ALL | unit | `uv run pytest tests/matmaster/tools/test_builtin_base.py -x` | ❌ W0 | ⬜ pending |
| 08-02-01 | 02 | 1 | TOOL-04 | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py -x` | ❌ W0 | ⬜ pending |
| 08-02-02 | 02 | 1 | TOOL-04 | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py::test_dangerous_command_blocked -x` | ❌ W0 | ⬜ pending |
| 08-02-03 | 02 | 1 | TOOL-07 | unit | `uv run pytest tests/matmaster/tools/test_listdir_tool.py -x` | ❌ W0 | ⬜ pending |
| 08-03-01 | 03 | 1 | TOOL-09 | unit | `uv run pytest tests/matmaster/tools/test_task_tools.py::test_task_create -x` | ❌ W0 | ⬜ pending |
| 08-03-02 | 03 | 1 | TOOL-09 | unit | `uv run pytest tests/matmaster/tools/test_task_tools.py::test_task_get -x` | ❌ W0 | ⬜ pending |
| 08-03-03 | 03 | 1 | TOOL-09 | unit | `uv run pytest tests/matmaster/tools/test_task_tools.py::test_task_list -x` | ❌ W0 | ⬜ pending |
| 08-03-04 | 03 | 1 | TOOL-09 | unit | `uv run pytest tests/matmaster/tools/test_task_tools.py::test_task_update -x` | ❌ W0 | ⬜ pending |
| 08-03-05 | 03 | 1 | TOOL-09 | unit | `uv run pytest tests/matmaster/tools/test_task_tools.py::test_task_complete -x` | ❌ W0 | ⬜ pending |
| 08-04-01 | 04 | 2 | ALL | unit | `uv run pytest tests/matmaster/core/test_exp.py -x` | ✅ 需补充 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/tools/test_builtin_base.py` — BuiltinTool ABC 基类测试（Protocol 满足、_require_session、错误处理）
- [ ] `tests/matmaster/tools/test_bash_tool.py` — BashTool 单元测试（执行、危险命令拦截）
- [ ] `tests/matmaster/tools/test_listdir_tool.py` — ListDirTool 单元测试
- [ ] `tests/matmaster/tools/test_task_tools.py` — 5 个 TaskTool 单元测试
- [ ] `tests/matmaster/tools/conftest.py` — 共享 fixtures（mock session、临时 workdir）

*Existing `tests/matmaster/core/test_exp.py` covers Exp integration — needs additional test cases for builtin tool registration.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
