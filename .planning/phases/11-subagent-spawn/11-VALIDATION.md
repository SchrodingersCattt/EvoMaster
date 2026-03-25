---
phase: 11
slug: subagent-spawn
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-25
---

# Phase 11 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `uv run pytest tests/ -x --tb=short -q` |
| **Full suite command** | `uv run pytest tests/ -v` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/ -x --tb=short -q`
- **After every plan wave:** Run `uv run pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | Test File | Status |
|---------|------|------|-------------|-----------|-------------------|-----------|--------|
| 11-01-01 | 01 | 1 | SUBA-01, SUBA-02, SUBA-04, PRMT-03 | unit | `uv run pytest tests/matmaster/tools/test_sub_agent_tool.py -x -v` | tests/matmaster/tools/test_sub_agent_tool.py | ⬜ pending |
| 11-02-01 | 02 | 2 | SUBA-01, SUBA-03, SUBA-05 | unit | `uv run pytest tests/matmaster/core/test_exp.py -x -v -k "sub_agent or spawn or source_override"` | tests/matmaster/core/test_exp.py | ⬜ pending |
| 11-02-02 | 02 | 2 | SUBA-03, SUBA-05 | integration | `uv run pytest tests/matmaster/integration/test_subagent_spawn.py -x -v` | tests/matmaster/integration/test_subagent_spawn.py | ⬜ pending |
| 11-03-01 | 03 | 3 | SUBA-05, SUBA-06 | unit | `uv run python -c "from src.utils.chat_event_source import normalize_event_source; assert normalize_event_source('MatMaster:explore') == 'MatMaster:explore'; print('OK')"` | (inline verify) | ⬜ pending |
| 11-03-02 | 03 | 3 | SUBA-06 | integration | `uv run pytest tests/matmaster/integration/test_subagent_event_routing.py -x -v` | tests/matmaster/integration/test_subagent_event_routing.py | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/tools/test_sub_agent_tool.py` — created by Plan 01 Task 1 (TDD: tests written first)
- [ ] `tests/matmaster/integration/test_subagent_spawn.py` — created by Plan 02 Task 2 (TDD: tests written first)
- [ ] `tests/matmaster/integration/test_subagent_event_routing.py` — created by Plan 03 Task 2 (TDD: tests written first)
- [ ] `tests/conftest.py` — shared fixtures (extend existing if present)

*All test files are created by TDD tasks within their respective plans. No separate Wave 0 plan needed.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Frontend observes sub-agent events in real-time | SUBA-06 | Requires running frontend + backend with SSE streaming | Start API server, open frontend, trigger sub-agent spawn, verify sub-agent events appear in chat UI |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
