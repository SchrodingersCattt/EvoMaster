---
phase: 21
slug: async-leaf-io-cleanup
status: complete
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-30
validated: 2026-03-30
---

# Phase 21 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio 0.24+ |
| **Config file** | pyproject.toml (`asyncio_mode = "auto"`) |
| **Quick run command** | `uv run pytest tests/matmaster/tools/test_bash_tool.py tests/matmaster/providers/test_openai_provider.py -x` |
| **Full suite command** | `uv run pytest` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/tools/test_bash_tool.py tests/matmaster/providers/test_openai_provider.py -x`
- **After every plan wave:** Run `uv run pytest`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 21-01-01 | 01 | 1 | TOOL-02a | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py::TestBashToolAsyncSubprocess::test_normal_command -x` | exists | green |
| 21-01-02 | 01 | 1 | TOOL-02b | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py::TestBashToolAsyncSubprocess::test_timeout -x` | exists | green |
| 21-01-03 | 01 | 1 | TOOL-02c | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py::TestBashToolAsyncSubprocess::test_dangerous_blocked -x` | exists | green |
| 21-01-04 | 01 | 1 | TOOL-02d | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py::TestBashToolAsyncSubprocess::test_is_input -x` | exists | green |
| 21-01-05 | 01 | 1 | TOOL-02e | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py::TestBashToolExecution -x` | exists | green |
| 21-01-06 | 01 | 1 | TOOL-02f | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py::TestBashToolExecution::test_session_not_injected_returns_error -x` | exists | green |
| 21-02-01 | 02 | 1 | PROV-cleanup | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestProtocolConformance -x` | exists | green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `tests/matmaster/tools/test_bash_tool.py::TestBashToolAsyncSubprocess` — new test class for native async subprocess path (4+ tests)
- [x] `tests/matmaster/providers/test_openai_provider.py` — remove `TestChatWithRetry` class + update `test_has_chat_with_retry_method`

*Existing infrastructure covers framework and fixture requirements.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Audit 2026-03-30

| Metric | Count |
|--------|-------|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |

Full suite: 1074 passed

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** validated 2026-03-30
