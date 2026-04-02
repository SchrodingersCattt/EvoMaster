---
phase: 34
slug: exp-service-hook
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-02
---

# Phase 34 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio |
| **Config file** | pyproject.toml |
| **Quick run command** | `uv run pytest tests/matmaster/core/test_agent_kernel.py tests/matmaster/core/test_agent_kernel_stream.py -q` |
| **Full suite command** | `uv run pytest tests/matmaster/ -q` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/core/test_agent_kernel.py tests/matmaster/core/test_agent_kernel_stream.py -q`
- **After every plan wave:** Run `uv run pytest tests/matmaster/ -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 34-01-01 | 01 | 1 | KGEN-06, ESIN-03, HRET-01, HRET-02, HRET-03, HRET-05 | unit | `uv run pytest tests/matmaster/core/test_agent_kernel.py tests/matmaster/core/test_agent_kernel_stream.py tests/matmaster/core/test_context_compactor.py -x -q` | Partial (W0 extend) | ⬜ pending |
| 34-01-02 | 01 | 1 | ESIN-01, ESIN-04, ESIN-05 | unit | `uv run pytest tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/core/test_agent_kernel.py tests/matmaster/core/test_agent_kernel_stream.py -x -q` | ❌ W0 | ⬜ pending |
| 34-02-01 | 02 | 2 | ESIN-02, ESIN-06, REGR-02 | integration | `uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/core/test_agent_kernel.py tests/matmaster/core/test_agent_kernel_extended.py -x -q` | ❌ W0 | ⬜ pending |
| 34-02-02 | 02 | 2 | ESIN-07 | unit | `uv run pytest tests/matmaster/integration/test_event_payloads.py -x -q` | Existing (extend) | ⬜ pending |
| 34-03-01 | 03 | 3 | HRET-06 | integration | `uv run pytest tests/matmaster/core/test_agent_kernel.py tests/matmaster/core/test_agent_kernel_stream.py -x -q` | Existing | ⬜ pending |
| 34-03-02 | 03 | 3 | HRET-04, HRET-06 | regression | `uv run pytest tests/matmaster/ -x -q` | Existing | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/core/test_exp_runtime_v2.py` — stubs for ESIN-01, ESIN-04 (build_runtime FullToolRunner injection + run_stream cleanup)
- [ ] `tests/matmaster/core/test_agent_kernel_stream.py` extension — stubs for ESIN-03, HRET-01, HRET-02, HRET-03 (_stream_llm_items segment-complete + event equivalence)
- [ ] `tests/matmaster/core/test_context_compactor.py` extension — stubs for HRET-05 (event_sink pattern, no Bus dependency)
- [ ] `tests/matmaster/integration/test_event_payloads.py` extension — stubs for ESIN-07 (ToolResult.payload -> info explicit mapping)
- [ ] `tests/matmaster/services/test_agent_run_stream.py` — stubs for ESIN-02 (run_agent_stream event bridging + source normalization)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| SSE streaming end-to-end | ESIN-02 | Requires live SSE connection + browser/curl | Start server, curl `/api/chat/stream`, verify event sequence |
| ChatHistoryConverter source compat | ESIN-06 | Requires DB with real chat history | Load existing session, verify source field renders correctly |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
