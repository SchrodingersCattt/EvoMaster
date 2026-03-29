---
phase: 18
slug: exp
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-29
---

# Phase 18 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x + pytest-asyncio |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `uv run pytest tests/matmaster/core/test_exp.py -x -q` |
| **Full suite command** | `uv run pytest tests/ -x -q` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/core/test_exp.py -x -q`
- **After every plan wave:** Run `uv run pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 18-01-01 | 01 | 1 | EXPL-01 | unit | `uv run pytest tests/matmaster/core/test_exp.py -x -q -k "assemble"` | ✅ | ⬜ pending |
| 18-01-02 | 01 | 1 | EXPL-02 | unit | `uv run pytest tests/matmaster/core/test_exp.py -x -q -k "build_runtime"` | ✅ | ⬜ pending |
| 18-01-03 | 01 | 1 | EXPL-03 | unit | `uv run pytest tests/matmaster/core/test_exp.py -x -q -k "run"` | ✅ | ⬜ pending |
| 18-02-01 | 02 | 1 | EXPL-04 | unit | `uv run pytest tests/matmaster/core/test_exp.py -x -q -k "spawn"` | ✅ | ⬜ pending |
| 18-02-02 | 02 | 1 | EXPL-04 | unit | `uv run pytest tests/matmaster/tools/builtin/test_spawn_tool.py -x -q` | ✅ | ⬜ pending |
| 18-03-01 | 03 | 2 | EXPL-03 | integration | `uv run pytest tests/matmaster/integration/ -x -q` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

*Existing infrastructure covers all phase requirements.*

- pytest-asyncio auto mode already configured (Phase 12)
- Async mock factories already in tests/conftest.py (Phase 12)
- All test files already exist from prior phases

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
