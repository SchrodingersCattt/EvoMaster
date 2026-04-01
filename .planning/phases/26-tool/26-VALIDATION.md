---
phase: 26
slug: tool
status: draft
nyquist_compliant: false
wave_0_complete: false
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
| 26-01-01 | 01 | 1 | TOOL-08 | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py -x` | Exists | ⬜ pending |
| 26-01-02 | 01 | 1 | TOOL-08 | unit | `uv run pytest tests/matmaster/tools/test_edit_tool.py -x` | Exists | ⬜ pending |
| 26-01-03 | 01 | 1 | TOOL-08 | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py -x -k dangerous` | ❌ W0 | ⬜ pending |
| 26-02-01 | 02 | 1 | TOOL-09 | unit | `uv run pytest tests/matmaster/tools/test_monitor_job.py -x` | ❌ W0 | ⬜ pending |
| 26-02-02 | 02 | 1 | TOOL-09 | unit | `uv run pytest tests/matmaster/tools/test_monitor_job.py -x -k schema` | ❌ W0 | ⬜ pending |
| 26-03-01 | 03 | 2 | TOOL-10 | unit | `uv run pytest tests/matmaster/tools/test_web_search_tool.py -x` | Exists | ⬜ pending |
| 26-03-02 | 03 | 2 | TOOL-07 | unit | `uv run pytest tests/matmaster/tools/test_tool_registry.py -x` | Exists | ⬜ pending |
| 26-03-03 | 03 | 2 | TOOL-07 | unit | `uv run python -c "from matmaster.tools import __all__; assert 'EvoToolAdapter' not in __all__"` | ❌ W0 | ⬜ pending |
| 26-03-04 | 03 | 2 | ALL | smoke | `uv run python -c "import matmaster.tools.builtin"` | ❌ W0 | ⬜ pending |
| 26-03-05 | 03 | 2 | ALL | smoke | `grep -r 'from evomaster.agent.tools.builtin\|from playground' matmaster/core/exp.py` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/tools/test_monitor_job.py` — MonitorJobTool as BuiltinTool: Protocol compliance, json_schema validity, _execute basic flow (mock job_service)
- [ ] `tests/matmaster/tools/test_bash_tool.py` — add `is_dangerous_bash_command` inline behavior tests (verify existing coverage first)
- [ ] Delete `tests/matmaster/tools/test_evomaster_tool_adapter.py` (test subject removed)
- [ ] Smoke test: `import matmaster.tools.builtin` must not trigger evomaster.agent.tools.builtin module load

*Existing test_bash_tool.py and test_edit_tool.py should continue passing after helper inlining.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
