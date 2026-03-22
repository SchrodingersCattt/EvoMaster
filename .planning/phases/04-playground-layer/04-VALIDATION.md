---
phase: 4
slug: playground-layer
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-22
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing, configured in `pytest.ini`) |
| **Config file** | `pytest.ini` |
| **Quick run command** | `uv run pytest tests/matmaster/playground/ tests/matmaster/types/test_context.py -x -q` |
| **Full suite command** | `uv run pytest tests/matmaster/ -x -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/playground/ tests/matmaster/types/test_context.py -x -q`
- **After every plan wave:** Run `uv run pytest tests/matmaster/ -x -q`
- **Before `$gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 04-01-01 | 01 | 1 | WKSP-01 | unit | `uv run pytest tests/matmaster/types/test_context.py -x -q` | Wave 0 | pending |
| 04-01-02 | 01 | 1 | WKSP-04 | unit | `uv run pytest tests/matmaster/playground/test_playground.py -x -q` | Wave 0 | pending |
| 04-02-01 | 02 | 2 | WKSP-02 | integration | `uv run pytest tests/matmaster/playground/test_playground_config_paths.py::test_mat_master_config_path -x -q` | Wave 0 | pending |
| 04-02-02 | 02 | 2 | WKSP-03 | integration | `uv run pytest tests/matmaster/playground/test_playground_config_paths.py::test_minimal_config_path -x -q` | Wave 0 | pending |
| 04-03-01 | 03 | 2 | WKSP-01 | unit | `uv run pytest tests/matmaster/assembly/test_evomaster_tool_adapter.py -x -q` | Wave 0 | pending |
| 04-03-02 | 03 | 2 | WKSP-01 | integration | `uv run pytest tests/matmaster/assembly/test_direct_exp.py tests/matmaster/assembly/test_exp.py -x -q` | Wave 0 | pending |

*Status: pending / green / red / flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/playground/__init__.py` — package init
- [ ] `tests/matmaster/playground/test_playground.py` — unified Playground prepare / cleanup coverage
- [ ] `tests/matmaster/playground/test_playground_config_paths.py` — config compatibility for `mat_master` and `minimal`
- [ ] `tests/matmaster/assembly/test_evomaster_tool_adapter.py` — adapter coverage for EvoMaster tools
- [ ] `tests/matmaster/types/test_context.py` — updated contract assertions for archival config

---

## Manual-Only Verifications

*All phase behaviors have automated verification. Real Bohrium / OSS integration stays in Phase 5 and is intentionally excluded from this phase's automated scope.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
