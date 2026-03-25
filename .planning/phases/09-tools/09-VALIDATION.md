---
phase: 9
slug: tools
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-25
---

# Phase 9 -- Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (via uv run) |
| **Config file** | `pytest.ini` |
| **Quick run command** | `uv run pytest tests/matmaster/tools/ -x --tb=short -q` |
| **Full suite command** | `uv run pytest tests/ -x --tb=short` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/tools/ -x --tb=short -q`
- **After every plan wave:** Run `uv run pytest tests/ -x --tb=short`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 09-01-T1 | 01 | 1 | TOOL-01, TOOL-02, TOOL-03, TOOL-08 | unit | `uv run pytest tests/matmaster/tools/test_read_tracker.py tests/matmaster/tools/test_read_tool.py tests/matmaster/tools/test_write_tool.py tests/matmaster/tools/test_edit_tool.py -x` | W0 | pending |
| 09-01-T2 | 01 | 1 | TOOL-01, TOOL-02, TOOL-03, TOOL-08 | unit | `uv run pytest tests/matmaster/tools/test_read_tracker.py tests/matmaster/tools/test_read_tool.py tests/matmaster/tools/test_write_tool.py tests/matmaster/tools/test_edit_tool.py -x` | W0 | pending |
| 09-02-T1 | 02 | 1 | TOOL-05, TOOL-06 | unit | `uv run pytest tests/matmaster/tools/test_glob_tool.py tests/matmaster/tools/test_grep_tool.py -x` | W0 | pending |
| 09-02-T2 | 02 | 1 | TOOL-05, TOOL-06 | unit | `uv run pytest tests/matmaster/tools/test_glob_tool.py tests/matmaster/tools/test_grep_tool.py -x` | W0 | pending |
| 09-03-T1 | 03 | 2 | TOOL-01, TOOL-02, TOOL-03, TOOL-05, TOOL-06, TOOL-08 | integration | `uv run pytest tests/matmaster/core/test_exp.py -x` | exists | pending |
| 09-03-T2 | 03 | 2 | TOOL-01, TOOL-02, TOOL-03, TOOL-05, TOOL-06, TOOL-08 | integration | `uv run pytest tests/matmaster/core/test_exp.py -x` | exists | pending |

*Status: pending / green / red / flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/tools/test_read_tracker.py` -- stubs for TOOL-08 (ReadTracker mark/check/clear/normalization)
- [ ] `tests/matmaster/tools/test_read_tool.py` -- stubs for TOOL-01 (ReadTool basic/line_range/format/tracker registration)
- [ ] `tests/matmaster/tools/test_write_tool.py` -- stubs for TOOL-02 (WriteTool create/overwrite/read-before-modify)
- [ ] `tests/matmaster/tools/test_edit_tool.py` -- stubs for TOOL-03 (EditTool str_replace/unique_match/read-before-modify)
- [ ] `tests/matmaster/tools/test_glob_tool.py` -- stubs for TOOL-05 (GlobTool find/workdir_safety/truncation)
- [ ] `tests/matmaster/tools/test_grep_tool.py` -- stubs for TOOL-06 (GrepTool grep/workdir_safety/truncation)
- [ ] `tests/matmaster/core/test_exp.py` -- exists; Plan 03 Task 2 updates with new integration assertions

*Existing infrastructure covers pytest framework. Wave 0 creates test stubs for Plan 01/02 tool tests. test_exp.py already exists and will be updated in Plan 03.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| *None* | -- | -- | -- |

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
