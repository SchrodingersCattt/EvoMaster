---
phase: 15
slug: hook
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-27
---

# Phase 15 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest >= 9.0.2 + pytest-asyncio >= 0.25.0 |
| **Config file** | pytest.ini (asyncio_mode=auto) |
| **Quick run command** | `uv run pytest tests/matmaster/core/test_hooks.py tests/matmaster/hooks/ -x` |
| **Full suite command** | `uv run pytest` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/core/test_hooks.py tests/matmaster/hooks/ -x`
- **After every plan wave:** Run `uv run pytest`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 15-01-01 | 01 | 1 | HOOK-01 | unit | `uv run pytest tests/matmaster/core/test_hooks.py -x` | ✅ needs async migration | ⬜ pending |
| 15-01-02 | 01 | 1 | HOOK-01 | unit | `uv run pytest tests/matmaster/hooks/test_output_processor.py -x` | ✅ needs async migration | ⬜ pending |
| 15-01-03 | 01 | 1 | HOOK-01 | unit | `uv run pytest tests/matmaster/hooks/test_assistant_state.py -x` | ✅ needs async migration | ⬜ pending |
| 15-01-04 | 01 | 1 | HOOK-01 | unit | `uv run pytest tests/matmaster/hooks/test_skill_hit.py -x` | ✅ needs async migration | ⬜ pending |
| 15-02-01 | 02 | 1 | HOOK-02 | unit | `uv run pytest tests/matmaster/hooks/test_confirmation.py -x` | ✅ needs full rewrite | ⬜ pending |
| 15-02-02 | 02 | 1 | HOOK-02 | integration | `uv run pytest tests/matmaster/integration/test_upstream_scenarios.py::TestCrossPodReplyQueue -x` | ✅ needs adaptation | ⬜ pending |
| 15-03-01 | 03 | 1 | HOOK-03 | unit | `uv run pytest tests/matmaster/core/test_hooks.py::TestEventEmitterHook -x` | ✅ needs async migration | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] Framework install: pytest-asyncio already installed, asyncio_mode=auto configured
- [x] tests/conftest.py: MockAsyncHook already exists (Phase 12)
- [ ] tests/matmaster/core/test_hooks.py: TrackingHook/SkipHook/StopHook custom Hook classes need async def migration
- [ ] tests/matmaster/hooks/test_confirmation.py: Full rewrite needed for Future-based async pattern
- [ ] tests/matmaster/integration/test_upstream_scenarios.py: TestCrossPodReplyQueue needs adaptation (_MockReplyQueue deprecated)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Cross-thread reply push (Worker mode) | HOOK-02 | Redis cross-pod communication requires live infrastructure | Deploy to staging, trigger confirmation flow, verify reply received across pods |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
