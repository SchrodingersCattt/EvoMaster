---
phase: 33
slug: toolrunner-toolscheduler
status: complete
nyquist_compliant: true
wave_0_complete: true
created: 2026-04-02
audited: 2026-04-02
---

# Phase 33 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `uv run pytest tests/matmaster/core/ -x -q --tb=short` |
| **Full suite command** | `uv run pytest tests/matmaster/core/test_structural_validation.py tests/matmaster/core/test_capability_policy.py tests/matmaster/core/test_tool_scheduler.py tests/matmaster/core/test_full_tool_runner.py tests/matmaster/core/test_builtin_claims.py tests/matmaster/sessions/test_session_capabilities.py tests/matmaster/tools/test_tool_compiler.py -x -q` |
| **Estimated runtime** | ~1 second |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/core/ -x -q --tb=short`
- **After every plan wave:** Run phase 33 full suite (see above)
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 1 second

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 33-01-01 | 01 | 1 | TRUN-03 | unit | `uv run pytest tests/matmaster/core/test_structural_validation.py -x` | ✅ | ✅ green (11 tests) |
| 33-01-02 | 01 | 1 | TCON-01 | unit | `uv run pytest tests/matmaster/core/test_capability_policy.py -x` | ✅ | ✅ green (11 tests) |
| 33-02-01 | 02 | 1 | TRUN-04 | unit | `uv run pytest tests/matmaster/core/test_tool_scheduler.py -x` | ✅ | ✅ green (10 tests) |
| 33-03-01 | 03 | 2 | TRUN-03 | integration | `uv run pytest tests/matmaster/core/test_full_tool_runner.py -x` | ✅ | ✅ green (13 tests) |
| 33-03-02 | 03 | 2 | D-09 | unit | `uv run pytest tests/matmaster/core/test_builtin_claims.py -x` | ✅ | ✅ green (15 tests) |
| 33-04-01 | 04 | 3 | TCON-03 | regression | `uv run pytest tests/matmaster/core/test_builtin_claims.py tests/matmaster/core/test_capability_policy.py -x` | ✅ | ✅ green |
| 33-05-01 | 05 | 3 | TCON-01 | unit | `uv run pytest tests/matmaster/sessions/test_session_capabilities.py -x` | ✅ | ✅ green (5 tests) |
| 33-05-02 | 05 | 3 | TRUN-03 | unit | `uv run pytest tests/matmaster/tools/test_tool_compiler.py -x` | ✅ | ✅ green (6 tests) |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `tests/matmaster/core/test_structural_validation.py` — TRUN-03 args_schema validation (11 tests)
- [x] `tests/matmaster/core/test_capability_policy.py` — TCON-01 effect_level / capability matching (11 tests)
- [x] `tests/matmaster/core/test_tool_scheduler.py` — TRUN-04 exclusive/shared_read/counted scheduling (10 tests)
- [x] `tests/matmaster/core/test_full_tool_runner.py` — TRUN-03 end-to-end execution chain (13 tests)

*All Wave 0 test files created during TDD execution across Plans 01-03.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| ToolScheduler timeout under load | TRUN-04 | Requires sustained concurrent requests | Run 10+ concurrent tool executions with exclusive resource, verify timeout after 60s |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 1s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved 2026-04-02

---

## Validation Audit 2026-04-02

| Metric | Count |
|--------|-------|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |
| Total tests | 74 |
| Requirements covered | 5 (TRUN-03, TRUN-04, TCON-01, TCON-03, D-09) |
| Manual-only items | 1 |

**Audit notes:** VALIDATION.md was in draft state from pre-execution. All 4 Wave 0 test files were created during TDD execution (Plans 01-03). Additional test files added during Plans 03-05 (test_builtin_claims.py, test_session_capabilities.py, test_tool_compiler.py). Full phase suite: 74 passed in 0.63s.
