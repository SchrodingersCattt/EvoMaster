---
phase: 21-async-leaf-io-cleanup
verified: 2026-03-30T08:00:00Z
status: passed
score: 7/7 must-haves verified
re_verification: null
gaps:
  - truth: "REQUIREMENTS.md TOOL-02 status updated to Complete"
    status: resolved
    reason: "REQUIREMENTS.md updated: TOOL-02 marked [x], traceability table status set to Complete, count updated to 35."
    artifacts:
      - path: ".planning/REQUIREMENTS.md"
        issue: "Line 27: still '- [ ] **TOOL-02**', line 116: still 'Pending', line 147: 'Complete: 33 (all except TOOL-02, HOOK-02)'"
    missing:
      - "Mark TOOL-02 as [ x ] in requirements checklist"
      - "Update status table row from Pending to Complete"
      - "Update summary line from 'Complete: 33' to 'Complete: 34'"
---

# Phase 21: Async Leaf IO Cleanup — Verification Report

**Phase Goal:** 完成叶子 I/O 层遗留 async 清理，落地 BashTool 原生 async subprocess 路径，并移除 provider 孤儿接口
**Verified:** 2026-03-30T08:00:00Z
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | BashTool 在 session-free 执行路径（evomaster LocalSession）使用 asyncio.create_subprocess_exec | VERIFIED | bash_tool.py:79 lazy-imports evomaster LocalSession, bash_tool.py:117 calls asyncio.create_subprocess_exec |
| 2 | session-dependent 路径（非 LocalSession）仍通过 super().execute() 走 to_thread 桥接 | VERIFIED | bash_tool.py:88 `return await super().execute(arguments)` — base class uses asyncio.to_thread |
| 3 | OpenAIProvider 已删除 chat_with_retry 孤儿接口 | VERIFIED | grep 0 matches in openai_provider.py; no `import time` either |
| 4 | OpenAIProvider 仍满足 LLMProvider Protocol (chat, chat_stream, __aenter__, __aexit__) | VERIFIED | isinstance(provider, LLMProvider) == True, validate_async_protocol returns [] |
| 5 | tool/provider 相关测试更新并通过 | VERIFIED | 12/12 BashTool tests pass, 50/50 OpenAIProvider tests pass, 1074 matmaster tests pass |
| 6 | BashTool async 路径正确处理 timeout (asyncio.wait_for + kill + exit code 124) | VERIFIED | bash_tool.py:125-135; test_timeout confirms kill() + wait() called |
| 7 | REQUIREMENTS.md 中 TOOL-02 状态更新为 Complete | FAILED | REQUIREMENTS.md 行 27 仍为 `[ ]`, 行 116 仍为 Pending，行 147 仍写 "Complete: 33" |

**Score:** 6/7 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `matmaster/tools/builtin/bash_tool.py` | BashTool with execute() override and _execute_async() native async subprocess | VERIFIED | async def execute() at line 72, async def _execute_async() at line 90, asyncio.create_subprocess_exec at line 117 |
| `tests/matmaster/tools/test_bash_tool.py` | TestBashToolAsyncSubprocess class with 5 tests | VERIFIED | Class at line 79, all 5 tests present (test_normal_command, test_timeout, test_dangerous_blocked, test_is_input, test_session_dependent_fallback) |
| `matmaster/providers/openai_provider.py` | OpenAIProvider without chat_with_retry, min 20 lines | VERIFIED | 286 lines, no chat_with_retry, no import time |
| `tests/matmaster/providers/test_openai_provider.py` | Provider tests without TestChatWithRetry class, min 20 lines | VERIFIED | No TestChatWithRetry class, no test_has_chat_with_retry_method |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `matmaster/tools/builtin/bash_tool.py` | `evomaster.agent.session.local` | isinstance check to choose async path | WIRED | Line 79: `from evomaster.agent.session.local import LocalSession as _MatmasterLocal`; line 81: `isinstance(self._session, _MatmasterLocal)` |
| `matmaster/tools/builtin/bash_tool.py` | `matmaster/tools/builtin/base.py` | super().execute() fallback | WIRED | Line 88: `return await super().execute(arguments)` |

**Note on naming discrepancy:** PLAN specified `matmaster.sessions.local.LocalSession` as the isinstance target, but the actual implementation correctly uses `evomaster.agent.session.local.LocalSession` — because `matmaster/core/playground.py:27` imports and instantiates this evomaster class in production. `matmaster.sessions.local.LocalSession` is a separate newer class used only by `matmaster/devshell/runner.py`. The wiring is functionally correct for the production code path; the PLAN simply mislabeled which LocalSession class is used by Playground.

### Data-Flow Trace (Level 4)

Not applicable — BashTool is an I/O tool (subprocess executor), not a data-rendering component. No dynamic data rendering path to trace.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| asyncio.create_subprocess_exec present in bash_tool | grep pattern match | Found at line 117 | PASS |
| chat_with_retry absent from openai_provider | grep 0 matches | 0 matches confirmed | PASS |
| LLMProvider isinstance check | uv run python3 -c "isinstance(...)" | True | PASS |
| All BashTool tests pass | uv run pytest test_bash_tool.py | 12/12 passed | PASS |
| All OpenAIProvider tests pass | uv run pytest test_openai_provider.py | 50/50 passed | PASS |
| Full matmaster test suite | uv run pytest tests/matmaster/ | 1074 passed, 3 skipped | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| TOOL-02 | 21-01-PLAN.md | BashTool 使用 asyncio.create_subprocess_exec 替代 subprocess.run | SATISFIED (code) / NOT UPDATED (docs) | Code: asyncio.create_subprocess_exec at bash_tool.py:117. REQUIREMENTS.md tracking not updated — still shows Pending |

**Orphaned requirements check:** No additional REQUIREMENTS.md IDs point to Phase 21 beyond TOOL-02.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `.planning/REQUIREMENTS.md` | 27, 116, 147 | TOOL-02 status not updated after implementation | Info | No functional impact; tracking document inconsistency only |

No code anti-patterns found in implemented files:
- No TODO/FIXME/placeholder in bash_tool.py or openai_provider.py
- No hardcoded empty returns
- No stub implementations

### SUMMARY Accuracy Note

The SUMMARY.md (lines 106-112) incorrectly states: "Kept execute() sync, used asyncio.run() internally to bridge to _execute_async()." The actual implementation uses `async def execute()` (bash_tool.py:72) which `await`s `_execute_async()` directly — no asyncio.run() bridge. The base class BuiltinTool.execute() is also `async def` (base.py:50). The SUMMARY's deviation description was inaccurate, but the code itself is correct and superior to the original plan approach.

### Human Verification Required

None — all critical behaviors verified programmatically.

### Gaps Summary

One gap identified: REQUIREMENTS.md was not updated to reflect TOOL-02 completion. The implementation is complete and correct:

- `matmaster/tools/builtin/bash_tool.py` implements the async dual-path via `async def execute()` override with `await _execute_async()` for evomaster LocalSession
- `matmaster/providers/openai_provider.py` has zero `chat_with_retry` references
- `matmaster/types/llm_provider.py` Protocol is clean (only chat, chat_stream, __aenter__, __aexit__)
- 1074 matmaster tests pass with zero regressions

The only action needed is updating `.planning/REQUIREMENTS.md` to mark TOOL-02 as complete.

---

_Verified: 2026-03-30T08:00:00Z_
_Verifier: Claude (gsd-verifier)_
