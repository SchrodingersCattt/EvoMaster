---
phase: 1
slug: foundation-contracts
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-21
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x + pytest-asyncio |
| **Config file** | pytest.ini (existing) |
| **Quick run command** | `python -m pytest tests/matmaster/types/ -x -q` |
| **Full suite command** | `python -m pytest tests/matmaster/ -v` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/matmaster/types/ -x -q`
- **After every plan wave:** Run `python -m pytest tests/matmaster/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| TBD | TBD | TBD | CONT-01 | unit | `pytest tests/matmaster/types/test_playground_context.py -v` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | CONT-02 | unit | `pytest tests/matmaster/types/test_agent_runtime_spec.py -v` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | CONT-03 | unit | `pytest tests/matmaster/types/test_events.py -v` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | CONT-04 | unit | `pytest tests/matmaster/types/test_guard.py -v` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | CONT-05 | unit | `pytest tests/matmaster/types/test_termination.py -v` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | EBUS-01 | unit | `pytest tests/matmaster/bus/test_message_bus.py -v` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | EBUS-02 | unit | `pytest tests/matmaster/bus/test_queue_bridge.py -v` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/__init__.py` — test package init
- [ ] `tests/matmaster/types/__init__.py` — contracts test subpackage
- [ ] `tests/matmaster/bus/__init__.py` — bus test subpackage
- [ ] `tests/matmaster/types/test_playground_context.py` — stubs for CONT-01
- [ ] `tests/matmaster/types/test_agent_runtime_spec.py` — stubs for CONT-02
- [ ] `tests/matmaster/types/test_events.py` — stubs for CONT-03
- [ ] `tests/matmaster/types/test_guard.py` — stubs for CONT-04
- [ ] `tests/matmaster/types/test_termination.py` — stubs for CONT-05
- [ ] `tests/matmaster/bus/test_message_bus.py` — stubs for EBUS-01
- [ ] `tests/matmaster/bus/test_queue_bridge.py` — stubs for EBUS-02

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| QueueBridge SSE compatibility | EBUS-02 | Requires running FastAPI SSE endpoint | Verify SSE events match current format via manual /stream call |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
