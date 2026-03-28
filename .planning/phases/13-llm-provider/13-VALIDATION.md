---
phase: 13
slug: llm-provider
status: complete
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-27
validated: 2026-03-28
---

# Phase 13 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio 1.3.0 |
| **Config file** | pytest.ini (asyncio_mode=auto) |
| **Quick run command** | `uv run pytest tests/matmaster/providers/ -x` |
| **Full suite command** | `uv run pytest tests/matmaster/providers/ tests/matmaster/core/test_context_compactor.py tests/matmaster/core/test_agent.py tests/matmaster/devshell/test_compaction_via_devshell.py -x` |
| **Estimated runtime** | ~0.7 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/providers/ tests/matmaster/core/test_context_compactor.py -x`
- **After every plan wave:** Run `uv run pytest tests/ -x --timeout=60`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 1 second

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 13-01-01 | 01 | 1 | LLMP-01 | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestChatContent -x` | ✓ | ✅ green |
| 13-01-02 | 01 | 1 | LLMP-02 | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestChatStreamContent -x` | ✓ | ✅ green |
| 13-01-03 | 01 | 1 | LLMP-02 | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestChatStreamExceptionTranslation -x` | ✓ | ✅ green |
| 13-02-01 | 02 | 1 | LLMP-03 | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestAsyncContextManager -x` | ✓ | ✅ green |
| 13-02-02 | 02 | 1 | LLMP-03 | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestProtocolConformance -x` | ✓ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `tests/matmaster/providers/test_openai_provider.py` — 55 async tests pass
- [x] `tests/matmaster/providers/test_llm_factory.py` — 7 factory tests pass (lazy init, no patches)
- [x] `tests/matmaster/core/test_context_compactor.py` — MockSummaryProvider/FailingSummaryProvider async, 20 pass + 1 skip (E2E deferred per D-08)
- [x] `tests/matmaster/providers/test_openai_provider.py::TestAsyncContextManager` — 5 lifecycle tests pass

*Framework already installed: pytest-asyncio 1.3.0 with asyncio_mode=auto*

---

## Additional Coverage (Plan 02)

- [x] `tests/matmaster/core/test_agent.py` — 39 tests pass, bridge loop + async providers
- [x] `tests/matmaster/devshell/test_compaction_via_devshell.py` — 28 pass + 2 skip (Kernel integration deferred per D-08)
- [x] `tests/matmaster/core/conftest.py` — MockLLMProvider async (aenter/aexit/chat/chat_stream)

---

## Skipped Tests (by design)

| Test | File | Reason |
|------|------|--------|
| TestEndToEndCompaction | test_context_compactor.py | E2E deferred to Phase 17-18 per D-08: requires Kernel async |
| TestKernelIntegration::test_kernel_triggers_compaction | test_compaction_via_devshell.py | Kernel integration deferred to Phase 17-18 per D-08 |
| TestKernelIntegration::test_kernel_without_compaction | test_compaction_via_devshell.py | Kernel integration deferred to Phase 17-18 per D-08 |

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 15s (actual: ~0.7s)
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** validated

---

## Validation Audit 2026-03-28

| Metric | Count |
|--------|-------|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |
| Tests passing | 142 |
| Tests skipped (by design) | 3 |
| Total test runtime | 0.7s |
