---
phase: 17
slug: agentkernel
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-29
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
| 17-01-01 | 01 | 1 | KERN-01 | unit | `uv run pytest tests/matmaster/core/test_agent.py -x` | Exists (needs async migration) | ⬜ pending |
| 17-01-02 | 01 | 1 | KERN-02 | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestCallLlmRetry -x` | Exists (needs async migration) | ⬜ pending |
| 17-01-03 | 01 | 1 | KERN-03 | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestFullCycle -x` | Exists (needs async migration) | ⬜ pending |
| 17-01-04 | 01 | 1 | KERN-04 | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestCompactorIntegration -x` | Exists (needs async migration) | ⬜ pending |
| 17-01-05 | 01 | 1 | KERN-05 | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestExternalCancel -x` | Exists (needs async migration) | ⬜ pending |
| 17-01-06 | 01 | 1 | KERN-06 | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestCallLlmRetry -x` | Exists (verify no time.sleep) | ⬜ pending |
| 17-02-01 | 02 | 2 | TEST-02 | integration | `uv run pytest tests/matmaster/core/test_agent.py -x` | Exists (needs migration) | ⬜ pending |
| 17-02-02 | 02 | 2 | TEST-03 | full suite | `uv run pytest` | N/A (gate check) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

*Existing infrastructure covers all phase requirements. pytest-asyncio auto mode and async mock factories are already in place from Phase 12.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
