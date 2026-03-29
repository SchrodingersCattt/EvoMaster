---
phase: 24
slug: emit-nowait-tech-debt
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-30
---

# Phase 24 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio >=0.24.0 |
| **Config file** | pyproject.toml (asyncio_mode = "auto") |
| **Quick run command** | `uv run pytest tests/matmaster/core/test_hooks.py tests/matmaster/hooks/ tests/matmaster/core/test_context_compactor.py tests/matmaster/core/test_bus.py -x` |
| **Full suite command** | `uv run pytest` |
| **Estimated runtime** | ~15 seconds (targeted), ~90 seconds (full) |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/core/test_hooks.py tests/matmaster/hooks/ tests/matmaster/core/test_context_compactor.py tests/matmaster/core/test_bus.py -x`
- **After every plan wave:** Run `uv run pytest`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 24-01-01 | 01 | 1 | HOOK-03-a | unit | `uv run pytest tests/matmaster/core/test_hooks.py::TestEventEmitterHook -x` | Yes | ⬜ pending |
| 24-01-02 | 01 | 1 | HOOK-03-b | unit | `uv run pytest tests/matmaster/core/test_hooks.py::TestEventEmitterHookSpawnId -x` | Yes | ⬜ pending |
| 24-01-03 | 01 | 1 | HOOK-03-c | unit | `uv run pytest tests/matmaster/hooks/test_assistant_state.py -x` | Yes | ⬜ pending |
| 24-01-04 | 01 | 1 | HOOK-03-d | unit | `uv run pytest tests/matmaster/hooks/test_output_processor.py -x` | Yes | ⬜ pending |
| 24-01-05 | 01 | 1 | HOOK-03-e | unit | `uv run pytest tests/matmaster/hooks/test_skill_hit.py -x` | Yes | ⬜ pending |
| 24-01-06 | 01 | 1 | HOOK-03-f | unit | `uv run pytest tests/matmaster/core/test_context_compactor.py::TestCompactorEventEmission -x` | Yes | ⬜ pending |
| 24-01-07 | 01 | 1 | HOOK-03-g | unit | `uv run pytest tests/matmaster/core/test_bus.py::TestMessageBusEmitNowait -x` | Yes | ⬜ pending |
| 24-01-08 | 01 | 1 | D-04 | static | Manual verification (grep for `stop_event: threading.Event`) | N/A | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements. No new test files needed.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| stop_event type annotation | D-04 | Static type annotation, not runtime behavior | `grep "stop_event.*threading.Event" src/services/agent_run_service.py` |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
