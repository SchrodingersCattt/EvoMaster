---
phase: 33
slug: toolrunner-toolscheduler
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-02
---

# Phase 33 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `uv run pytest tests/matmaster/core/ -x -q --tb=short` |
| **Full suite command** | `uv run pytest tests/ -x --tb=short` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/core/ -x -q --tb=short`
- **After every plan wave:** Run `uv run pytest tests/ -x --tb=short`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 33-01-01 | 01 | 1 | TRUN-03 | unit | `uv run pytest tests/matmaster/core/test_structural_validation.py -x` | ❌ W0 | ⬜ pending |
| 33-01-02 | 01 | 1 | TCON-01 | unit | `uv run pytest tests/matmaster/core/test_capability_policy.py -x` | ❌ W0 | ⬜ pending |
| 33-02-01 | 02 | 1 | TRUN-04 | unit | `uv run pytest tests/matmaster/core/test_tool_scheduler.py -x` | ❌ W0 | ⬜ pending |
| 33-03-01 | 03 | 2 | TRUN-03 | integration | `uv run pytest tests/matmaster/core/test_full_tool_runner.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/core/test_structural_validation.py` — stubs for TRUN-03 args_schema validation
- [ ] `tests/matmaster/core/test_capability_policy.py` — stubs for TCON-01 effect_level / capability matching
- [ ] `tests/matmaster/core/test_tool_scheduler.py` — stubs for TRUN-04 exclusive/shared_read/counted scheduling
- [ ] `tests/matmaster/core/test_full_tool_runner.py` — stubs for TRUN-03 end-to-end execution chain (no Exp activation; Exp activation moved to Phase 34 ESIN-04)

*Existing pytest infrastructure covers framework requirements.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| ToolScheduler timeout under load | TRUN-04 | Requires sustained concurrent requests | Run 10+ concurrent tool executions with exclusive resource, verify timeout after 60s |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
