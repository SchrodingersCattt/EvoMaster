---
phase: 15
slug: hook
status: complete
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-27
validated: 2026-03-28
---

# Phase 15 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest >= 9.0.2 + pytest-asyncio >= 0.25.0 |
| **Config file** | pyproject.toml (asyncio_mode=auto) |
| **Quick run command** | `uv run pytest tests/matmaster/core/test_hooks.py tests/matmaster/hooks/ -x` |
| **Full suite command** | `uv run pytest` |
| **Estimated runtime** | ~0.65 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/core/test_hooks.py tests/matmaster/hooks/ -x`
- **After every plan wave:** Run `uv run pytest`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 1 second

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 15-01-01 | 01 | 1 | HOOK-01 | unit | `uv run pytest tests/matmaster/core/test_hooks.py -x` | ✅ | ✅ green |
| 15-01-02 | 01 | 1 | HOOK-01 | unit | `uv run pytest tests/matmaster/hooks/test_output_processor.py -x` | ✅ | ✅ green |
| 15-01-03 | 01 | 1 | HOOK-01 | unit | `uv run pytest tests/matmaster/hooks/test_assistant_state.py -x` | ✅ | ✅ green |
| 15-01-04 | 01 | 1 | HOOK-01 | unit | `uv run pytest tests/matmaster/hooks/test_skill_hit.py -x` | ✅ | ✅ green |
| 15-01-05 | 01 | 1 | HOOK-01 | unit | `uv run pytest tests/matmaster/devshell/test_stream_hook.py -x` | ✅ | ✅ green |
| 15-02-01 | 02 | 2 | HOOK-02 | unit | `uv run pytest tests/matmaster/hooks/test_confirmation.py -x` | ✅ | ✅ green |
| 15-02-02 | 02 | 2 | HOOK-02 | integration | `uv run pytest tests/matmaster/integration/test_upstream_scenarios.py::TestCrossPodReplyQueue -x` | ✅ | ✅ green |
| 15-03-01 | 03 | 1 | HOOK-03 | unit | `uv run pytest tests/matmaster/core/test_hooks.py::TestEventEmitterHook -x` | ✅ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Test Count by Requirement

| Requirement | Test Files | Test Count | Coverage |
|-------------|-----------|------------|----------|
| HOOK-01 | test_hooks.py, test_output_processor.py, test_assistant_state.py, test_skill_hit.py, test_stream_hook.py | 47 | COVERED |
| HOOK-02 | test_confirmation.py, test_upstream_scenarios.py::TestCrossPodReplyQueue | 15 | COVERED |
| HOOK-03 | test_hooks.py::TestEventEmitterHook, TestEventEmitterHookSpawnId | 9 | COVERED |
| **Total** | **8 test files** | **98** | **All COVERED** |

---

## Wave 0 Requirements

- [x] Framework install: pytest-asyncio installed, asyncio_mode=auto configured in pyproject.toml
- [x] tests/conftest.py: MockAsyncHook exists (Phase 12)
- [x] tests/matmaster/core/test_hooks.py: All Hook classes migrated to async def
- [x] tests/matmaster/hooks/test_confirmation.py: Full rewrite for Future-based async pattern
- [x] tests/matmaster/integration/test_upstream_scenarios.py: TestCrossPodReplyQueue rewritten for async Future model

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Cross-thread reply push (Worker mode) | HOOK-02 | Redis cross-pod communication requires live infrastructure | Deploy to staging, trigger confirmation flow, verify reply received across pods |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** PASSED

---

## Validation Audit 2026-03-28

| Metric | Count |
|--------|-------|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |
| Total tests | 98 |
| All green | yes |
