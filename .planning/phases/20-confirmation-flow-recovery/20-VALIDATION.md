---
phase: 20
slug: confirmation-flow-recovery
status: complete
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-30
validated: 2026-03-30
---

# Phase 20 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio 0.24+ |
| **Config file** | pyproject.toml (`asyncio_mode = "auto"`) |
| **Quick run command** | `uv run pytest tests/matmaster/hooks/test_confirmation.py tests/matmaster/integration/test_upstream_scenarios.py -x` |
| **Full suite command** | `uv run pytest` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/hooks/test_confirmation.py tests/matmaster/integration/test_upstream_scenarios.py -x`
- **After every plan wave:** Run `uv run pytest`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 20-01-01 | 01 | 1 | HOOK-02a | unit | `uv run pytest tests/matmaster/hooks/test_confirmation.py -x` | exists | green |
| 20-01-02 | 01 | 1 | HOOK-02b | integration | `uv run pytest tests/matmaster/hooks/test_confirmation.py::TestConfirmationHookAdapter -x` | exists | green |
| 20-02-01 | 02 | 2 | HOOK-02c | integration | `uv run pytest tests/matmaster/integration/test_upstream_scenarios.py::TestAgentRunServiceConfirmationRecovery -x` | exists | green |
| 20-02-02 | 02 | 2 | HOOK-02d | integration | `uv run pytest tests/matmaster/integration/test_upstream_scenarios.py::test_confirmation_reply_bridge_thread_exits_with_redis_compatible_timeout -x` | exists | green |

*Status: pending / green / red / flaky*

---

## Wave 0 Requirements

- [x] tests/matmaster/hooks/test_confirmation.py rewritten for Future-based model
- [x] TestAgentRunServiceConfirmationRecovery class created

*All Wave 0 tests implemented and passing.*

---

## Manual-Only Verifications

All behaviors have automated verification.

---

## Validation Audit 2026-03-30

| Metric | Count |
|--------|-------|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |

Full suite: 1074 passed

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** validated 2026-03-30
