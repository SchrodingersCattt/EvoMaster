---
phase: 14
slug: tool
status: validated
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-27
validated: 2026-03-28
---

# Phase 14 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x + pytest-asyncio |
| **Config file** | pyproject.toml |
| **Quick run command** | `uv run pytest tests/matmaster/tools/ -x -q` |
| **Full suite command** | `uv run pytest tests/ -x` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/tools/ -x -q`
- **After every plan wave:** Run `uv run pytest tests/ -x`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 14-01-01 | 01 | 1 | TOOL-01 | unit | `uv run pytest tests/matmaster/tools/test_builtin_base.py -x` | ✅ | ✅ green |
| 14-01-02 | 01 | 1 | TOOL-02 | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py -x` | ✅ | ✅ green |
| 14-01-03 | 01 | 1 | TOOL-03 | unit | `uv run pytest tests/matmaster/tools/test_read_tool.py tests/matmaster/tools/test_edit_tool.py -x` | ✅ | ✅ green |
| 14-01-04 | 01 | 1 | TOOL-04 | unit | `uv run pytest tests/matmaster/tools/test_spawn_tool.py -x` | ✅ | ✅ green |
| 14-01-05 | 01 | 1 | TOOL-05 | integration | `uv run pytest tests/matmaster/tools/ -x` | ✅ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

*Existing infrastructure covers all phase requirements.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** validated

---

## Validation Audit 2026-03-28

| Metric | Count |
|--------|-------|
| Gaps found | 1 |
| Resolved | 1 |
| Escalated | 0 |

**Details:**
- test_task_tools.py: 5 orphaned tests removed (called non-existent `TaskStore.update()`/`complete()`), 12 tests fixed to match actual API signatures
- Full suite: 266 tests passing (was 154 passed + 1 failed)
