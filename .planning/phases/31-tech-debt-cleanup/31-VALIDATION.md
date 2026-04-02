---
phase: 31
slug: tech-debt-cleanup
status: draft
nyquist_compliant: false
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
| 31-01-01 | 01 | 1 | SC-1 (Session mock) | unit | `uv run --extra dev python -m pytest tests/matmaster/ -x -q --tb=short -k "session"` | ✅ | ⬜ pending |
| 31-01-02 | 01 | 1 | SC-1 (BohriumSetupService) | unit | `uv run --extra dev python -m pytest tests/matmaster/ -x -q --tb=short -k "bohrium"` | ✅ | ⬜ pending |
| 31-01-03 | 01 | 1 | SC-1 (LLMProvider mock) | unit | `uv run --extra dev python -m pytest tests/matmaster/ -x -q --tb=short -k "compactor"` | ✅ | ⬜ pending |
| 31-02-01 | 02 | 1 | SC-2 (isolation script) | smoke | `bash scripts/test_matmaster_isolation.sh` | ✅ | ⬜ pending |
| 31-03-01 | 03 | 2 | SC-3 (REQUIREMENTS.md) | manual | Inspect REQUIREMENTS.md | ✅ | ⬜ pending |
| 31-03-02 | 03 | 2 | SC-4 (docstring cleanup) | manual | `grep evomaster matmaster/adaptors/calculation/job_service.py` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements. No new tests needed, only existing test fixes.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| REQUIREMENTS.md checkboxes all [x] | SC-3 | Document inspection | `grep -c '^\- \[ \]' .planning/REQUIREMENTS.md` should return 0 |
| job_service.py no evomaster refs | SC-4 | Docstring inspection | `grep -c evomaster matmaster/adaptors/calculation/job_service.py` should return 0 |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
