---
phase: 31
slug: tech-debt-cleanup
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-04-02
---

# Phase 31 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio 0.25.x |
| **Config file** | `pyproject.toml` [tool.pytest] |
| **Quick run command** | `uv run --extra dev python -m pytest tests/matmaster/ -x -q --tb=short` |
| **Full suite command** | `uv run --extra dev python -m pytest tests/matmaster/ -q --tb=short` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run --extra dev python -m pytest tests/matmaster/ -x -q --tb=short`
- **After every plan wave:** Run `uv run --extra dev python -m pytest tests/matmaster/ -q --tb=short`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 31-01-01 | 01 | 1 | SC-1 (Session mock) | unit | `uv run --extra dev python -m pytest tests/matmaster/ -x -q --tb=short -k "session"` | yes | pending |
| 31-01-02 | 01 | 1 | SC-1 (BohriumSetupService) | unit | `uv run --extra dev python -m pytest tests/matmaster/ -x -q --tb=short -k "bohrium"` | yes | pending |
| 31-02-01 | 02 | 2 | SC-2 (isolation script) | smoke | `bash scripts/test_matmaster_isolation.sh` | yes | pending |
| 31-02-02 | 02 | 2 | SC-3 (REQUIREMENTS.md) | automated | `test "$(grep -c '^\- \[ \]' .planning/REQUIREMENTS.md)" = "0"` | yes | pending |
| 31-02-03 | 02 | 2 | SC-4 (docstring + comment cleanup) | automated | `! grep -q evomaster matmaster/adaptors/calculation/job_service.py && ! grep -q 'evomaster mixin' matmaster/core/playground.py` | yes | pending |

*Status: pending / green / red / flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements. No new tests needed, only existing test fixes.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| (none) | | All verifications have automated commands | |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved
