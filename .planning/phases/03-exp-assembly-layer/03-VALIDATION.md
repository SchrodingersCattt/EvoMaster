---
phase: 3
slug: exp-assembly-layer
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-22
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing, configured in pytest.ini) |
| **Config file** | `pytest.ini` (pythonpath=., testpaths=tests, asyncio_mode=auto) |
| **Quick run command** | `python -m pytest tests/matmaster/assembly/ -x -q` |
| **Full suite command** | `python -m pytest tests/matmaster/ -x -q` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/matmaster/assembly/ -x -q`
- **After every plan wave:** Run `python -m pytest tests/matmaster/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 03-01-01 | 01 | 0 | ASBL-02 | unit | `python -m pytest tests/matmaster/assembly/test_tool_registry.py -x` | Wave 0 | pending |
| 03-01-02 | 01 | 0 | ASBL-05 | unit | `python -m pytest tests/matmaster/assembly/test_context_builder.py -x` | Wave 0 | pending |
| 03-01-03 | 01 | 0 | ASBL-01 | unit | `python -m pytest tests/matmaster/assembly/test_exp.py -x` | Wave 0 | pending |
| 03-01-04 | 01 | 0 | ASBL-01 | integration | `python -m pytest tests/matmaster/assembly/test_direct_exp.py -x` | Wave 0 | pending |
| 03-01-05 | 01 | 0 | ASBL-03 | integration | `python -m pytest tests/matmaster/assembly/test_guard_injection.py -x` | Wave 0 | pending |
| 03-01-06 | 01 | 0 | ASBL-06 | unit | `python -m pytest tests/matmaster/assembly/test_worker_registry.py -x` | Wave 0 | pending |

*Status: pending / green / red / flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/assembly/__init__.py` — package init
- [ ] `tests/matmaster/assembly/conftest.py` — shared fixtures (MockTool, MockSkillRegistry, mock PlaygroundContext builder)
- [ ] `tests/matmaster/assembly/test_tool_registry.py` — covers ASBL-02
- [ ] `tests/matmaster/assembly/test_context_builder.py` — covers ASBL-05
- [ ] `tests/matmaster/assembly/test_exp.py` — covers ASBL-01, ASBL-04
- [ ] `tests/matmaster/assembly/test_direct_exp.py` — covers ASBL-01 integration
- [ ] `tests/matmaster/assembly/test_guard_injection.py` — covers ASBL-03
- [ ] `tests/matmaster/assembly/test_worker_registry.py` — covers ASBL-06

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
