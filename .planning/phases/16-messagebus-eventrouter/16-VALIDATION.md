---
phase: 16
slug: messagebus-eventrouter
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-28
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
| 16-01-T1 | 01 | 1 | INFR-01,INFR-02,INFR-03 | unit+integration | `uv run pytest tests/matmaster/core/test_bus.py tests/matmaster/integration/test_event_router.py -x -q` | Exists (migrate sync->async + add emit_nowait) | ⬜ pending |
| 16-01-T2 | 01 | 1 | INFR-02,INFR-03 | integration | `uv run pytest tests/matmaster/integration/test_event_router.py -x -q` | Exists (migrate threading->asyncio, handlers async) | ⬜ pending |
| 16-02-T1 | 02 | 2 | INFR-01 | integration | `uv run pytest tests/matmaster/ -x -q` | ✅ (emit callers use await) | ⬜ pending |
| 16-02-T2 | 02 | 2 | INFR-02,INFR-03 | integration | `uv run pytest tests/matmaster/ -x -q` | ✅ (service bridge for router) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/core/test_bus.py` — 8 tests migrate sync->async (queue.Empty -> asyncio.QueueEmpty, sync def -> async def)
- [ ] `tests/matmaster/core/test_bus.py::test_emit_nowait` — add new test for emit_nowait sync method
- [ ] `tests/matmaster/integration/test_event_router.py::TestEventRouter` — 7 tests migrate threading->asyncio (Thread -> Task, time.sleep -> asyncio.sleep)
- [ ] `tests/matmaster/integration/test_event_router.py::TestPersistenceHandler` — 14 tests migrate to async def + await handle
- [ ] `tests/matmaster/integration/test_event_router.py::TestSSEHandler` — 18 tests migrate to async def + await handle + AsyncMock send_cb; delete test_async_send_with_loop and test_sync_send_without_loop
- [ ] `tests/matmaster/integration/test_workspace_handler.py` — update after WorkspaceHandler.handle() async migration

*Baseline: 58 tests all PASS (1.86s). Post-migration target: >= 57 tests (net: +1 emit_nowait, -2 obsolete dual-path tests).*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
