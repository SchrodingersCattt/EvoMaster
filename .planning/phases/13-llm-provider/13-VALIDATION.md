---
phase: 13
slug: llm-provider
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-27
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
| **Full suite command** | `uv run pytest tests/matmaster/providers/ tests/matmaster/core/test_context_compactor.py -x` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/providers/ tests/matmaster/core/test_context_compactor.py -x`
- **After every plan wave:** Run `uv run pytest tests/ -x --timeout=60`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 13-01-01 | 01 | 1 | LLMP-01 | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestChatContent -x` | ❌ W0 | ⬜ pending |
| 13-01-02 | 01 | 1 | LLMP-02 | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestChatStreamContent -x` | ❌ W0 | ⬜ pending |
| 13-01-03 | 01 | 1 | LLMP-02 | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestChatStreamExceptionTranslation -x` | ❌ W0 | ⬜ pending |
| 13-02-01 | 02 | 1 | LLMP-03 | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestAsyncContextManager -x` | ❌ W0 | ⬜ pending |
| 13-02-02 | 02 | 1 | LLMP-03 | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestProtocolConformance -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/providers/test_openai_provider.py` — 改造为 async 测试（现有 28 个测试）
- [ ] `tests/matmaster/providers/test_llm_factory.py` — 适配新的 __init__ 不创建 client（现有 7 个测试）
- [ ] `tests/matmaster/core/test_context_compactor.py` — MockSummaryProvider/FailingSummaryProvider 改 async（现有 20 个测试）
- [ ] `tests/matmaster/providers/test_openai_provider.py::TestAsyncContextManager` — 新增生命周期测试类

*Framework already installed: pytest-asyncio 1.3.0 with asyncio_mode=auto*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
