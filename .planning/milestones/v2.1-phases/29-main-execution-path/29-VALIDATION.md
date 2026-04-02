---
phase: 29
slug: main-execution-path
status: validated
nyquist_compliant: true
wave_0_complete: true
created: 2026-04-01
validated: 2026-04-02
---

# Phase 29 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (pyproject.toml asyncio_mode=auto) |
| **Config file** | pytest.ini + pyproject.toml [tool.pytest.ini_options] |
| **Quick run command** | `uv run pytest tests/matmaster/test_import_audit.py -x` |
| **Full suite command** | `uv run pytest tests/ -x --ignore=tests/playground --ignore=tests/evaluation` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/test_import_audit.py tests/test_workspace_resolver.py -x`
- **After every plan wave:** Run `uv run pytest tests/ -x --ignore=tests/playground --ignore=tests/evaluation`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 29-01-01 | 01 | 1 | CONS-01 | unit (import audit) | `uv run pytest tests/matmaster/test_import_audit.py -x` | ✅ | ✅ green |
| 29-01-02 | 01 | 1 | CONS-01 | unit (import audit) | `uv run pytest tests/matmaster/test_import_audit.py::TestNoEvomasterSessionImportsInMatmaster -x` | ✅ | ✅ green |
| 29-02-01 | 02 | 1 | CONS-01 | unit | `uv run pytest tests/test_workspace_resolver.py -x` | ✅ | ✅ green |
| 29-02-02 | 02 | 1 | CONS-01 | smoke | `uv run pytest --collect-only 2>&1 \| grep -i error` | ✅ | ✅ green |
| 29-03-01 | 03 | 2 | CONS-02 | integration | `uv run pytest tests/ -x --ignore=tests/playground --ignore=tests/evaluation` | ✅ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `tests/matmaster/test_import_audit.py` — add evomaster.config + evomaster.utils audit rules (TestNoEvomasterConfigImportsInMatmaster, TestNoEvomasterUtilsImportsInMatmaster)
- [x] Remove xfail markers on bash_tool import audit tests (xfail removed in Plan 01 Task 2)

*All Wave 0 items completed during Plan 01 execution.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| pytest --collect-only no errors | CONS-01 | Smoke test for broken imports | Run `uv run pytest --collect-only 2>&1 \| grep -i error` — expect empty output |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 30s (0.74s actual)
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** validated

## Validation Audit 2026-04-02

| Metric | Count |
|--------|-------|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |
| Tests passing | 17/17 |
| evomaster runtime imports in matmaster/ | 0 |
| pytest collection errors | 0 |
