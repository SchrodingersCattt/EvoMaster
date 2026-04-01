---
phase: 26
slug: tool
status: complete
nyquist_compliant: true
wave_0_complete: true
created: 2026-04-01
---

# Phase 26 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2 + pytest-asyncio (auto mode) |
| **Config file** | pytest.ini |
| **Quick run command** | `uv run pytest tests/matmaster/tools/ -x -q` |
| **Full suite command** | `uv run pytest tests/matmaster/ -x -q` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/tools/ -x -q`
- **After every plan wave:** Run `uv run pytest tests/matmaster/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 26-01-01 | 01 | 1 | TOOL-08 | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py -x` | Exists | ✅ green |
| 26-01-02 | 01 | 1 | TOOL-08 | unit | `uv run pytest tests/matmaster/tools/test_edit_tool.py -x` | Exists | ✅ green |
| 26-01-03 | 01 | 1 | TOOL-08 | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py -x -k dangerous` | Exists | ✅ green |
| 26-02-01 | 02 | 1 | TOOL-09 | unit | `uv run pytest tests/matmaster/tools/test_monitor_job.py -x` | Exists | ✅ green |
| 26-02-02 | 02 | 1 | TOOL-09 | unit | `uv run pytest tests/matmaster/tools/test_monitor_job.py -x -k schema` | Exists | ✅ green |
| 26-03-01 | 03 | 2 | TOOL-10 | unit | `uv run pytest tests/matmaster/tools/test_web_search_tool.py -x` | Exists | ✅ green |
| 26-03-02 | 03 | 2 | TOOL-07 | unit | `uv run pytest tests/matmaster/tools/test_tool_registry.py -x` | Exists | ✅ green |
| 26-03-03 | 03 | 2 | TOOL-07 | unit | `uv run python -c "from matmaster.tools import __all__; assert 'EvoToolAdapter' not in __all__"` | Exists | ✅ green |
| 26-03-04 | 03 | 2 | ALL | smoke | `uv run python -c "import matmaster.tools.builtin"` | Exists | ✅ green |
| 26-03-05 | 03 | 2 | ALL | smoke | `grep -r 'from evomaster.agent.tools.builtin\|from playground' matmaster/core/exp.py` | Exists | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `tests/matmaster/tools/test_monitor_job.py` — MonitorJobTool as BuiltinTool: Protocol compliance, json_schema validity (19 tests, all green)
- [x] `tests/matmaster/tools/test_bash_tool.py` — `is_dangerous_bash_command` inline behavior covered (2 dangerous-specific tests green)
- [x] Delete `tests/matmaster/tools/test_evomaster_tool_adapter.py` (test subject removed, file deleted in Plan 03)
- [x] Smoke test: `import matmaster.tools.builtin` does not trigger evomaster.agent.tools.builtin module load

*Existing test_bash_tool.py and test_edit_tool.py continue passing after helper inlining.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 30s (suite runs in ~0.65s)
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** PASSED

## Validation Audit 2026-04-01

| Metric | Count |
|--------|-------|
| Gaps found | 2 |
| Resolved | 2 |
| Escalated | 0 |

- Gap 26-02-01: Created `test_monitor_job.py` with Protocol compliance tests (9 tests)
- Gap 26-02-02: Created schema validity tests in same file (8 tests)
- Bonus: 2 import cleanliness tests verifying no evomaster.agent.tools.builtin pollution
