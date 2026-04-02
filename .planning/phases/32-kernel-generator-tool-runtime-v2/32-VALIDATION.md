---
phase: 32
slug: kernel-generator-tool-runtime-v2
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-02
---

# Phase 32 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2+ / pytest-asyncio 0.24.0+ |
| **Config file** | pyproject.toml `[tool.pytest.ini_options]` asyncio_mode = "auto" |
| **Quick run command** | `uv run python -m pytest tests/matmaster/core/test_agent_kernel.py -x -q` |
| **Full suite command** | `uv run python -m pytest tests/matmaster/ -x -q` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run python -m pytest tests/matmaster/core/test_agent_kernel.py tests/matmaster/core/test_agent_kernel_extended.py -x -q`
- **After every plan wave:** Run `uv run python -m pytest tests/matmaster/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 32-01-01 | 01 | 1 | KGEN-01 | unit | `uv run python -m pytest tests/matmaster/core/test_agent_kernel_stream.py -x -q` | ❌ W0 | ⬜ pending |
| 32-01-02 | 01 | 1 | KGEN-02 | unit | `uv run python -m pytest tests/matmaster/core/test_agent_kernel_stream.py -x -q` | ❌ W0 | ⬜ pending |
| 32-01-03 | 01 | 1 | KGEN-03 | regression | `uv run python -m pytest tests/matmaster/core/test_agent_kernel.py tests/matmaster/core/test_agent_kernel_extended.py -x -q` | ✅ (39 tests) | ⬜ pending |
| 32-01-04 | 01 | 1 | KGEN-04 | unit | `uv run python -m pytest tests/matmaster/core/test_agent_kernel_stream.py -x -q` | ❌ W0 | ⬜ pending |
| 32-01-05 | 01 | 1 | KGEN-05 | unit | `uv run python -m pytest tests/matmaster/core/test_agent_kernel_stream.py -x -q` | ❌ W0 | ⬜ pending |
| 32-02-01 | 02 | 1 | TOBJ-01 | unit | `uv run python -m pytest tests/matmaster/types/test_topology.py -x -q` | ❌ W0 | ⬜ pending |
| 32-02-02 | 02 | 1 | TOBJ-02 | unit | `uv run python -m pytest tests/matmaster/types/test_topology.py -x -q` | ❌ W0 | ⬜ pending |
| 32-02-03 | 02 | 1 | TOBJ-03 | unit | `uv run python -m pytest tests/matmaster/types/test_topology.py -x -q` | ❌ W0 | ⬜ pending |
| 32-02-04 | 02 | 1 | TOBJ-04 | unit | `uv run python -m pytest tests/matmaster/types/test_tool_spec.py -x -q` | ❌ W0 | ⬜ pending |
| 32-02-05 | 02 | 1 | TOBJ-05 | unit | `uv run python -m pytest tests/matmaster/types/test_tool_spec.py -x -q` | ❌ W0 | ⬜ pending |
| 32-02-06 | 02 | 1 | TOBJ-06 | unit | `uv run python -m pytest tests/matmaster/types/test_tool_spec.py -x -q` | ❌ W0 | ⬜ pending |
| 32-02-07 | 02 | 1 | TOBJ-07 | unit | `uv run python -m pytest tests/matmaster/types/test_tool_spec.py -x -q` | ❌ W0 | ⬜ pending |
| 32-02-08 | 02 | 1 | TOBJ-08 | unit | `uv run python -m pytest tests/matmaster/types/test_tool_decision.py -x -q` | ❌ W0 | ⬜ pending |
| 32-03-01 | 03 | 1 | TCAT-01 | unit | `uv run python -m pytest tests/matmaster/tools/test_tool_catalog.py -x -q` | ❌ W0 | ⬜ pending |
| 32-03-02 | 03 | 1 | TCAT-02 | unit | `uv run python -m pytest tests/matmaster/tools/test_tool_catalog.py -x -q` | ❌ W0 | ⬜ pending |
| 32-03-03 | 03 | 1 | TCAT-03 | unit | `uv run python -m pytest tests/matmaster/tools/test_tool_catalog.py -x -q` | ❌ W0 | ⬜ pending |
| 32-04-01 | 04 | 2 | TRUN-01 | unit | `uv run python -m pytest tests/matmaster/core/test_tool_runner.py -x -q` | ❌ W0 | ⬜ pending |
| 32-04-02 | 04 | 2 | TRUN-02 | unit | `uv run python -m pytest tests/matmaster/core/test_tool_runner.py -x -q` | ❌ W0 | ⬜ pending |
| 32-04-03 | 04 | 2 | TRUN-05 | integration | `uv run python -m pytest tests/matmaster/core/test_agent_kernel_stream.py -x -q` | ❌ W0 | ⬜ pending |
| 32-05-01 | 05 | 2 | TCON-02 | regression | `uv run python -m pytest tests/matmaster/core/test_guard_pipeline.py -x -q` | ✅ | ⬜ pending |
| 32-05-02 | 05 | 2 | TRES-01 | unit | `uv run python -m pytest tests/matmaster/tools/test_tool_result.py -x -q` | ✅ (needs update) | ⬜ pending |
| 32-05-03 | 05 | 2 | SPEC-01 | unit | `uv run python -m pytest tests/matmaster/types/test_runtime.py -x -q` | ✅ (needs update) | ⬜ pending |
| 32-05-04 | 05 | 2 | TDEF-01 | unit | `uv run python -m pytest tests/matmaster/core/test_agent_kernel_stream.py -x -q` | ❌ W0 | ⬜ pending |
| 32-06-01 | 06 | 3 | REGR-01 | regression | `uv run python -m pytest tests/matmaster/core/test_agent_kernel.py tests/matmaster/core/test_agent_kernel_extended.py -v` | ✅ (39 tests) | ⬜ pending |
| 32-06-02 | 06 | 3 | REGR-03 | regression | `uv run python -m pytest tests/matmaster/tools/test_bash_tool.py tests/matmaster/tools/test_read_tool.py -x -q` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/types/test_topology.py` — stubs for TOBJ-01, TOBJ-02, TOBJ-03
- [ ] `tests/matmaster/types/test_tool_spec.py` — stubs for TOBJ-04, TOBJ-05, TOBJ-06, TOBJ-07
- [ ] `tests/matmaster/types/test_tool_decision.py` — stubs for TOBJ-08
- [ ] `tests/matmaster/tools/test_tool_catalog.py` — stubs for TCAT-01, TCAT-02, TCAT-03
- [ ] `tests/matmaster/core/test_tool_runner.py` — stubs for TRUN-01, TRUN-02
- [ ] `tests/matmaster/core/test_agent_kernel_stream.py` — stubs for KGEN-01~05, TRUN-05, TDEF-01
- [ ] Update `tests/matmaster/tools/test_tool_result.py` — sync TRES-01 (info -> payload + meta)
- [ ] Update `tests/matmaster/types/test_events.py` — sync ToolResultEvent.info -> payload
- [ ] Update `tests/matmaster/hooks/test_output_processor.py` — sync result.info -> result.payload
- [ ] Update `tests/matmaster/core/test_hooks.py` — sync event.info -> event.payload

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
