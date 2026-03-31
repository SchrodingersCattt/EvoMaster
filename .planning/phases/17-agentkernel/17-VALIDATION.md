---
phase: 17
slug: agentkernel
status: complete
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-29
validated: 2026-03-29
---

# Phase 17 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x + pytest-asyncio 1.3.0 |
| **Config file** | pyproject.toml `[tool.pytest.ini_options]` |
| **Quick run command** | `uv run pytest tests/matmaster/core/test_agent.py -x` |
| **Full suite command** | `uv run pytest` |
| **Estimated runtime** | ~60 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/core/test_agent.py -x`
- **After every plan wave:** Run `uv run pytest tests/matmaster/ -x`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 60 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 17-01-01 | 01 | 1 | KERN-01 | unit | `uv run pytest tests/matmaster/core/test_agent.py -x` | ✓ 35 async tests | ✅ green |
| 17-01-02 | 01 | 1 | KERN-02 | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestCallLlmRetry -x` | ✓ async `await _call_llm` | ✅ green |
| 17-01-03 | 01 | 1 | KERN-03 | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestFullCycle -x` | ✓ async tool execution | ✅ green |
| 17-01-04 | 01 | 1 | KERN-04 | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestCompactorIntegration -x` | ✓ async compactor | ✅ green |
| 17-01-05 | 01 | 1 | KERN-05 | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestExternalCancel -x` | ✓ threading.Event | ✅ green |
| 17-01-06 | 01 | 1 | KERN-06 | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestCallLlmRetry -x` | ✓ no time.sleep | ✅ green |
| 17-02-01 | 02 | 2 | TEST-02 | integration | `uv run pytest tests/matmaster/ -x` | ✓ 1057 passed | ✅ green |
| 17-02-02 | 02 | 2 | TEST-03 | full suite | `uv run pytest` | ✓ 1057 passed, 3 skipped | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

*Existing infrastructure covers all phase requirements. pytest-asyncio auto mode and async mock factories are already in place from Phase 12.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 60s (0.03s for unit tests, 71s for full suite)
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** validated 2026-03-29

## Validation Audit 2026-03-29

| Metric | Count |
|--------|-------|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |

All 8 requirements verified via automated tests. Source code assertions confirmed KERN-01 through KERN-06. Full suite: 1057 passed, 3 skipped, 0 failures.
