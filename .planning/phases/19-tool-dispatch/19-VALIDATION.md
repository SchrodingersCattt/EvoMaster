---
phase: 19
slug: tool-dispatch
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-29
---

# Phase 19 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| **Config file** | pyproject.toml (`asyncio_mode = "auto"`) |
| **Quick run command** | `uv run pytest tests/matmaster/core/test_agent.py -x -q` |
| **Full suite command** | `uv run pytest tests/matmaster/ -x -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/core/test_agent.py -x -q`
- **After every plan wave:** Run `uv run pytest tests/matmaster/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 19-01-01 | 01 | 1 | BRDG-01 | integration | `uv run pytest tests/matmaster/integration/test_e2e_minimal.py -x` | Existing (needs adaptation) | ⬜ pending |
| 19-01-02 | 01 | 1 | BRDG-02 | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestExternalCancel -x` | Existing (no change needed) | ⬜ pending |
| 19-02-01 | 02 | 1 | TOOL-06 | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestParallelToolDispatch -x` | New test needed | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/core/test_agent.py::TestParallelToolDispatch` — stubs for TOOL-06 parallel execution
- [ ] `tests/matmaster/core/test_agent.py::TestParallelToolDispatch::test_parallel_faster_than_serial` — timing assertion
- [ ] `tests/matmaster/core/test_agent.py::TestParallelToolDispatch::test_gather_return_exceptions` — exception handling per D-05
- [ ] `tests/matmaster/core/test_agent.py::TestParallelToolDispatch::test_preserves_tool_call_order` — message ordering

*Existing infrastructure covers BRDG-01 and BRDG-02. Only TOOL-06 requires new test stubs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| DevShell asyncio.run() wrapper | SC-4 | Requires interactive shell session | Run `uv run python -c "from matmaster.core.playground import PlaygroundManager; ..."` and verify no RuntimeError |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
