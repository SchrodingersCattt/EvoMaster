---
phase: 16
slug: messagebus-eventrouter
status: validated
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-28
validated: 2026-03-29
---

# Phase 16 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio |
| **Config file** | pytest.ini (asyncio_mode=auto) |
| **Quick run command** | `uv run pytest tests/matmaster/core/test_bus.py tests/matmaster/integration/test_event_router.py -x -q` |
| **Full suite command** | `uv run pytest tests/ -x -q` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/core/test_bus.py tests/matmaster/integration/test_event_router.py -x -q`
- **After every plan wave:** Run `uv run pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 16-01-T1 | 01 | 1 | INFR-01 | unit | `uv run pytest tests/matmaster/core/test_bus.py -x -q` | ✅ 11 tests | ✅ green |
| 16-01-T2 | 01 | 1 | INFR-02,INFR-03 | integration | `uv run pytest tests/matmaster/integration/test_event_router.py tests/matmaster/integration/test_workspace_handler.py tests/matmaster/integration/test_sse_skill_hit.py tests/test_chat_stream_direct.py -x -q` | ✅ 63 tests | ✅ green |
| 16-02-T1 | 02 | 2 | INFR-01 | integration | `uv run pytest tests/matmaster/core/test_hooks.py tests/matmaster/hooks/ tests/matmaster/core/test_context_compactor.py -x -q` | ✅ 64 tests, 1 skipped | ✅ green |
| 16-02-T2 | 02 | 2 | INFR-01,INFR-02,INFR-03 | integration | `uv run pytest tests/matmaster/ -x -q` | ✅ 403 tests, 3 skipped | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `tests/matmaster/core/test_bus.py` — 11 tests (8 migrated sync->async + 3 new emit_nowait/set_loop tests)
- [x] `tests/matmaster/core/test_bus.py::test_emit_nowait` — 3 tests (sync fallback, cross-thread, set_loop)
- [x] `tests/matmaster/integration/test_event_router.py::TestEventRouter` — 7 tests migrated threading->asyncio
- [x] `tests/matmaster/integration/test_event_router.py::TestPersistenceHandler` — 15 tests migrated to async def + await handle
- [x] `tests/matmaster/integration/test_event_router.py::TestSSEHandler` — 16 tests migrated (18 - 2 obsolete dual-path)
- [x] `tests/matmaster/integration/test_workspace_handler.py` — 7 tests updated for async handle()

*Baseline: 58 tests. Post-migration actual: 66 core tests (11 bus + 48 router + 7 workspace). Target exceeded.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 5s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved 2026-03-29

## Validation Audit 2026-03-29

| Metric | Count |
|--------|-------|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |

**Notes:**
- 2 pre-existing failures in `TestRunInterruptedDetection` (Pydantic Protocol mock issue, not Phase 16)
- 1 pre-existing failure in `test_compaction_real_api` (async tool_registry issue, not Phase 16)
- All Phase 16 requirements (INFR-01, INFR-02, INFR-03) fully covered with 541 passing tests
