---
phase: 10
slug: tool-description-system-prompt
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-25
---

# Phase 10 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (via uv run pytest) |
| **Config file** | pytest.ini |
| **Quick run command** | `uv run pytest tests/matmaster/tools/test_tool_descriptions.py -x` |
| **Full suite command** | `uv run pytest tests/matmaster/ -x` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/tools/test_tool_descriptions.py -x`
- **After every plan wave:** Run `uv run pytest tests/matmaster/ -x`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 10-01-01 | 01 | 1 | PRMT-01 | unit | `uv run pytest tests/matmaster/tools/test_tool_descriptions.py -x` | Wave 0 | pending |
| 10-01-02 | 01 | 1 | PRMT-01 | unit | `uv run pytest tests/matmaster/tools/test_tool_descriptions.py::test_description_token_budget -x` | Wave 0 | pending |
| 10-01-03 | 01 | 1 | PRMT-01 | unit | `uv run pytest tests/matmaster/tools/test_tool_descriptions.py::test_schema_param_descriptions -x` | Wave 0 | pending |
| 10-01-04 | 01 | 1 | PRMT-01 | unit | `uv run pytest tests/matmaster/tools/test_tool_descriptions.py::test_routing_consistency -x` | Wave 0 | pending |
| 10-02-01 | 02 | 1 | PRMT-02 | unit | `uv run pytest tests/matmaster/core/test_context_builder.py -x` | exists, extend | pending |
| 10-02-02 | 02 | 1 | PRMT-02 | integration | `uv run pytest tests/matmaster/integration/test_direct_toml_prompt.py -x` | Wave 0 | pending |
| 10-02-03 | 02 | 1 | PRMT-02 | integration | `uv run pytest tests/matmaster/core/test_exp.py -x` | exists, extend | pending |

*Status: pending / green / red / flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/tools/test_tool_descriptions.py` — 12 tool description format/length/routing consistency tests
- [ ] `tests/matmaster/integration/test_direct_toml_prompt.py` — direct.toml developer_instructions content verification

*Existing infrastructure covers PRMT-02-a (test_context_builder.py) and PRMT-02-c (test_exp.py) partially — may need extension.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| DevShell multi-turn tool calling accuracy | PRMT-02 SC3 | Requires LLM interaction | Launch DevShell, send file-operation tasks, verify correct tool selection over 3+ turns |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
