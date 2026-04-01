---
phase: 30
slug: decoupling-audit
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-02
---

# Phase 30 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x + pytest-asyncio (auto mode) |
| **Config file** | `pytest.ini` |
| **Quick run command** | `uv run python -m pytest tests/matmaster/test_import_audit.py -x -q` |
| **Full suite command** | `uv run python -m pytest tests/matmaster/ -q --tb=short` |
| **Estimated runtime** | ~30 seconds (quick), ~120 seconds (full) |

---

## Sampling Rate

- **After every task commit:** Run `uv run python -m pytest tests/matmaster/test_import_audit.py -x -q`
- **After every plan wave:** Run `uv run python -m pytest tests/matmaster/ -q --tb=short`
- **Before `/gsd:verify-work`:** Full suite must be green (`uv run python -m pytest -q --tb=short`)
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 30-01-01 | 01 | 0 | QUAL-06 | unit (AST) | `uv run python -m pytest tests/matmaster/test_import_audit.py -x -q` | Partial (needs extension) | ⬜ pending |
| 30-01-02 | 01 | 0 | QUAL-07 | integration | `bash scripts/test_matmaster_isolation.sh` | ❌ W0 | ⬜ pending |
| 30-01-03 | 01 | 0 | QUAL-07 | unit | `uv run python -m pytest tests/matmaster/ -q --tb=short` | ✅ (needs import fixes) | ⬜ pending |
| 30-02-01 | 02 | 1 | QUAL-06 | unit (AST) | `uv run python -m pytest tests/matmaster/test_import_audit.py -x -q` | ✅ (after W0 extension) | ⬜ pending |
| 30-02-02 | 02 | 1 | QUAL-07 | integration | `bash scripts/test_matmaster_isolation.sh` | ✅ (after W0) | ⬜ pending |
| 30-03-01 | 03 | 2 | QUAL-08 | manual (file) | `test -f docs/decoupling-migration-v2.1.md && echo PASS` | ❌ W0 | ⬜ pending |
| 30-03-02 | 03 | 2 | ALL | regression | `uv run python -m pytest -q --tb=short` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/test_import_audit.py` — extend with TestPhase30FullIsolation class (full coverage for all matmaster/ modules)
- [ ] `scripts/test_matmaster_isolation.sh` — isolation test script (mv evomaster/ + src/ aside, run tests/matmaster/, restore)
- [ ] Fix 7 evomaster imports in 5 test files (replace with matmaster equivalents)
- [ ] Fix 28 src imports in 10 test files (convert to pytest.importorskip conditional imports)

*Existing test_import_audit.py covers 7 prefixes with 15 tests. Wave 0 extends to full Phase 30 coverage.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Migration doc completeness | QUAL-08 | Document quality requires human judgment | Review docs/decoupling-migration-v2.1.md for: compat layer list, remaining paths, cleanup order |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
