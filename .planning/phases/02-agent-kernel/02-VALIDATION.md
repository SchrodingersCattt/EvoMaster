---
phase: 2
slug: agent-kernel
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-22
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (already configured) |
| **Config file** | pytest.ini |
| **Quick run command** | `pytest tests/matmaster/kernel/ -x -q` |
| **Full suite command** | `pytest tests/matmaster/ -x -q` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/matmaster/kernel/ -x -q`
- **After every plan wave:** Run `pytest tests/matmaster/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 02-01-01 | 01 | 1 | KERN-01 | unit | `pytest tests/matmaster/kernel/test_kernel.py -x` | ❌ W0 | ⬜ pending |
| 02-01-02 | 01 | 1 | KERN-01 | unit | `pytest tests/matmaster/kernel/test_kernel.py::test_natural_finish -x` | ❌ W0 | ⬜ pending |
| 02-01-03 | 01 | 1 | KERN-01 | unit | `pytest tests/matmaster/kernel/test_kernel.py::test_max_turns -x` | ❌ W0 | ⬜ pending |
| 02-01-04 | 01 | 1 | KERN-01 | unit | `pytest tests/matmaster/kernel/test_kernel.py::test_cancel -x` | ❌ W0 | ⬜ pending |
| 02-02-01 | 02 | 1 | KERN-02 | unit | `pytest tests/matmaster/kernel/test_guard_pipeline.py::test_loop_detection -x` | ❌ W0 | ⬜ pending |
| 02-02-02 | 02 | 1 | KERN-02 | unit | `pytest tests/matmaster/kernel/test_guard_pipeline.py::test_builtin_not_removable -x` | ❌ W0 | ⬜ pending |
| 02-03-01 | 02 | 1 | KERN-03 | unit | `pytest tests/matmaster/kernel/test_guard_pipeline.py::test_pipeline_order -x` | ❌ W0 | ⬜ pending |
| 02-03-02 | 02 | 1 | KERN-03 | unit | `pytest tests/matmaster/kernel/test_guard_pipeline.py::test_first_deny -x` | ❌ W0 | ⬜ pending |
| 02-04-01 | 03 | 1 | KERN-04 | unit | `pytest tests/matmaster/kernel/test_hooks.py::test_pre_tool_call_skip -x` | ❌ W0 | ⬜ pending |
| 02-04-02 | 03 | 1 | KERN-04 | unit | `pytest tests/matmaster/kernel/test_hooks.py::test_should_continue_false -x` | ❌ W0 | ⬜ pending |
| 02-04-03 | 03 | 1 | KERN-04 | unit | `pytest tests/matmaster/kernel/test_hooks.py::test_hook_short_circuit -x` | ❌ W0 | ⬜ pending |
| 02-04-04 | 03 | 1 | KERN-04 | unit | `pytest tests/matmaster/kernel/test_hooks.py::test_event_emitter_hook -x` | ❌ W0 | ⬜ pending |
| 02-05-01 | 04 | 1 | LLMP-01 | unit | `pytest tests/matmaster/kernel/test_llm_provider.py::test_protocol_check -x` | ❌ W0 | ⬜ pending |
| 02-05-02 | 04 | 1 | LLMP-01 | unit | `pytest tests/matmaster/kernel/test_llm_provider.py::test_chat -x` | ❌ W0 | ⬜ pending |
| 02-05-03 | 04 | 1 | LLMP-01 | unit | `pytest tests/matmaster/kernel/test_llm_provider.py::test_chat_stream -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/kernel/__init__.py` — package init
- [ ] `tests/matmaster/kernel/test_kernel.py` — covers KERN-01
- [ ] `tests/matmaster/kernel/test_guard_pipeline.py` — covers KERN-02, KERN-03
- [ ] `tests/matmaster/kernel/test_hooks.py` — covers KERN-04
- [ ] `tests/matmaster/kernel/test_llm_provider.py` — covers LLMP-01
- [ ] `tests/matmaster/kernel/test_types.py` — covers Message types
- [ ] `tests/matmaster/kernel/conftest.py` — shared fixtures (MockLLMProvider, mock spec builder)

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
