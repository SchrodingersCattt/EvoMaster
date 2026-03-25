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

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 11-01-01 | 01 | 1 | SUBA-01 | unit | `uv run pytest tests/test_subagent_tool.py -k spawn_fn` | ❌ W0 | ⬜ pending |
| 11-01-02 | 01 | 1 | SUBA-02 | unit | `uv run pytest tests/test_subagent_tool.py -k config` | ❌ W0 | ⬜ pending |
| 11-02-01 | 02 | 1 | SUBA-03 | unit | `uv run pytest tests/test_subagent_tool.py -k recursion` | ❌ W0 | ⬜ pending |
| 11-02-02 | 02 | 1 | SUBA-04 | unit | `uv run pytest tests/test_subagent_tool.py -k cancel` | ❌ W0 | ⬜ pending |
| 11-03-01 | 03 | 2 | SUBA-05 | unit | `uv run pytest tests/test_subagent_tool.py -k event_routing` | ❌ W0 | ⬜ pending |
| 11-03-02 | 03 | 2 | SUBA-06 | unit | `uv run pytest tests/test_subagent_tool.py -k source_normalize` | ❌ W0 | ⬜ pending |
| 11-03-03 | 03 | 2 | PRMT-03 | unit | `uv run pytest tests/test_subagent_tool.py -k system_prompt` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_subagent_tool.py` — test stubs for SUBA-01 through SUBA-06, PRMT-03
- [ ] `tests/conftest.py` — shared fixtures (extend existing if present)

*Existing test infrastructure (pytest, conftest) covers framework needs. Wave 0 adds test file stubs only.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Frontend observes sub-agent events in real-time | SUBA-05 | Requires running frontend + backend with SSE streaming | Start API server, open frontend, trigger sub-agent spawn, verify sub-agent events appear in chat UI |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
