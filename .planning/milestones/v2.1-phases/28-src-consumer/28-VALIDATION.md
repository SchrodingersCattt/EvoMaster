---
phase: 28
slug: src-consumer
status: audited
nyquist_compliant: true
wave_0_complete: true
created: 2026-04-01
audited: 2026-04-01
---

# Phase 28 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (auto mode) |
| **Config file** | pyproject.toml `[tool.pytest.ini_options]` |
| **Quick run command** | `uv run pytest tests/matmaster/test_import_audit.py -x -q` |
| **Full suite command** | `uv run pytest tests/matmaster/ -x -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/test_import_audit.py tests/matmaster/integration/test_events_to_messages.py -x -q`
- **After every plan wave:** Run `uv run pytest tests/matmaster/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 28-01-01 | 01 | 1 | INVR-01 | unit (AST audit) | `uv run pytest tests/matmaster/test_import_audit.py -x -q` | ✅ exists | ✅ green |
| 28-01-02 | 01 | 1 | INVR-02 | unit (AST audit + bohrium_env) | `uv run pytest tests/matmaster/test_import_audit.py tests/matmaster/test_bohrium_env.py -x -q` | ✅ exists | ✅ green |
| 28-02-01 | 02 | 2 | INVR-01, INVR-02 | unit (callback injection) | `uv run pytest tests/matmaster/test_bohrium_setup_injection.py -x -q` | ✅ exists (6 tests) | ✅ green |
| 28-02-02 | 02 | 2 | INVR-01, INVR-02 | unit (import audit strict) | `uv run pytest tests/matmaster/test_import_audit.py -x -q` | ✅ exists | ✅ green |
| 28-03-01 | 03 | 3 | CONS-03 | unit (behavior + format) | `uv run pytest tests/matmaster/integration/test_events_to_messages.py tests/test_chat_history_reasoning_state.py tests/test_chat_history_repair.py -x -q` | ✅ exists (31 tests) | ✅ green |
| 28-03-02 | 03 | 3 | CONS-04 | unit (import audit) | `uv run pytest tests/matmaster/test_import_audit.py -x -q` | ✅ exists | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `tests/matmaster/test_import_audit.py` — 删除 `TestExpectedLazyBohrimImportsExist`，新增 `TestNoSrcImportsInMatmaster`、`TestNoEvomasterSessionImportsInMatmaster`、`TestNoEvomasterEnvBohriumImportsAnywhere`
- [x] `tests/matmaster/integration/test_events_to_messages.py` — 17 个 baseline 测试全部 pass
- [x] `tests/matmaster/test_bohrium_setup_injection.py` — 回调注入 unit test（6 tests）
- [x] `tests/test_chat_history_reasoning_state.py` + `tests/test_chat_history_repair.py` — model_dump 输出格式断言，matmaster flat tool_calls 格式验证（审计修复：5 个测试从 evomaster 格式更新为 matmaster 格式）

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| DB 历史事件 tool_calls 格式兼容 | CONS-03 | 需要真实 DB 数据验证两种格式 | 1. 查询包含 assistant_state 的历史事件 2. 确认 _adapt_tool_calls_format 能同时处理 nested 和 flat 格式 |

---

## Validation Audit 2026-04-01

| Metric | Count |
|--------|-------|
| Gaps found | 5 |
| Resolved | 5 |
| Escalated | 0 |

**Root cause:** Root-level test files (`tests/test_chat_history_reasoning_state.py`, `tests/test_chat_history_repair.py`) were not included in Phase 28 test suite and their expectations were stale after chat_history.py migrated from evomaster to matmaster message types.

**Fixes applied:**
- `reasoning_content` assertion: `meta.reasoning_content` → top-level field
- `tool_calls` format: nested `function.name` → flat `name`
- `_tool_call()` helper: evomaster nested → matmaster flat format
- `ToolMessage` content: dict → str

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 15s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved 2026-04-01
