---
phase: 19
slug: tool-dispatch
status: complete
nyquist_compliant: true
wave_0_complete: true
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
| 19-01-01 | 01 | 1 | BRDG-01 | integration | `uv run pytest tests/matmaster/integration/test_e2e_minimal.py -x` | ✓ exists | ✅ green |
| 19-01-02 | 01 | 1 | BRDG-02 | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestExternalCancel -x` | ✓ exists | ✅ green |
| 19-02-01 | 02 | 1 | TOOL-06 | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestParallelToolDispatch -x` | ✓ exists (6 tests) | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `tests/matmaster/core/test_agent.py::TestParallelToolDispatch` — 6 tests covering TOOL-06
- [x] `tests/matmaster/core/test_agent.py::TestParallelToolDispatch::test_parallel_execution_faster_than_serial` — timing assertion
- [x] `tests/matmaster/core/test_agent.py::TestParallelToolDispatch::test_gather_return_exceptions` — exception handling per D-05
- [x] `tests/matmaster/core/test_agent.py::TestParallelToolDispatch::test_preserves_tool_call_order` — message ordering
- [x] `tests/matmaster/core/test_agent.py::TestParallelToolDispatch::test_mixed_blocked_skipped_executed_order` — mixed states
- [x] `tests/matmaster/core/test_agent.py::TestParallelToolDispatch::test_single_tool_call_unchanged` — regression
- [x] `tests/matmaster/core/test_agent.py::TestParallelToolDispatch::test_exception_in_closure_not_gather` — closure exception

*All Wave 0 tests implemented and passing.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| DevShell asyncio.run() wrapper | SC-4 | Requires interactive shell session | Run `uv run python -c "from matmaster.core.playground import PlaygroundManager; ..."` and verify no RuntimeError |

---

## Validation Audit 2026-03-29

| Metric | Count |
|--------|-------|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |

Full suite: 1063 passed, 3 skipped, 0 failures (76s)

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 15s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** ✓ validated 2026-03-29
