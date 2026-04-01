---
phase: 29
slug: main-execution-path
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-01
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
| 29-01-01 | 01 | 1 | CONS-01 | unit (import audit) | `uv run pytest tests/matmaster/test_import_audit.py -x` | ✅ (needs enhancement) | ⬜ pending |
| 29-01-02 | 01 | 1 | CONS-01 | unit (import audit) | `uv run pytest tests/matmaster/test_import_audit.py::TestNoEvomasterSessionImportsInMatmaster -x` | ✅ (needs xfail removal) | ⬜ pending |
| 29-02-01 | 02 | 1 | CONS-01 | unit | `uv run pytest tests/test_workspace_resolver.py -x` | ✅ (needs import fix) | ⬜ pending |
| 29-02-02 | 02 | 1 | CONS-01 | smoke | `uv run pytest --collect-only 2>&1 \| grep -i error` | Manual | ⬜ pending |
| 29-03-01 | 03 | 2 | CONS-02 | integration | `uv run pytest tests/ -x` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/test_import_audit.py` — add evomaster.config + evomaster.utils audit rules (currently only checks mcp/calculation/session/env.bohrium/src)
- [ ] Remove xfail markers on bash_tool import audit tests (after bash_tool cleanup)

*Existing infrastructure covers most requirements; Wave 0 enhances import audit coverage.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| pytest --collect-only no errors | CONS-01 | Smoke test for broken imports | Run `uv run pytest --collect-only 2>&1 \| grep -i error` — expect empty output |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
