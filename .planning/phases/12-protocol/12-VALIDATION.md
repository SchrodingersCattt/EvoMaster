---
phase: 12
slug: protocol
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-26
---

# Phase 12 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2 + pytest-asyncio 1.3.0 (needs install) |
| **Config file** | `pytest.ini` (asyncio_mode=auto already configured) |
| **Quick run command** | `uv run pytest tests/matmaster/types/ tests/matmaster/core/test_hooks.py -x` |
| **Full suite command** | `uv run pytest` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/types/ tests/matmaster/core/test_hooks.py tests/matmaster/tools/test_tool_registry.py -x`
- **After every plan wave:** Run `uv run pytest`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 12-01-01 | 01 | 0 | TEST-01 | smoke | `uv run pytest --co` | NO (Wave 0) | ⬜ pending |
| 12-01-02 | 01 | 0 | PROT-05 | unit | `uv run pytest tests/matmaster/test_validation.py -x` | NO (Wave 0) | ⬜ pending |
| 12-02-01 | 02 | 1 | PROT-01 | unit | `uv run pytest tests/matmaster/types/test_llm_provider.py -x` | YES (needs update) | ⬜ pending |
| 12-03-01 | 03 | 1 | PROT-02 | unit | `uv run pytest tests/matmaster/tools/test_tool_registry.py tests/matmaster/tools/test_builtin_base.py -x` | YES (needs update) | ⬜ pending |
| 12-04-01 | 04 | 1 | PROT-03 | unit | `uv run pytest tests/matmaster/core/test_hooks.py -x` | YES (needs update) | ⬜ pending |
| 12-05-01 | 05 | 1 | PROT-04 | unit | `uv run pytest tests/matmaster/types/test_guards.py -x` | YES (verify unchanged) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `pytest-asyncio` install: `uv add --group dev pytest-asyncio>=1.3.0`
- [ ] `tests/matmaster/test_validation.py` — validation helper unit tests (covers PROT-05, TEST-01)
- [ ] `tests/conftest.py` — async mock factories (MockAsyncLLMProvider, MockAsyncTool, MockAsyncHook)

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
