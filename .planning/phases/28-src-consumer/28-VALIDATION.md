---
phase: 28
slug: src-consumer
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-01
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
| 28-01-01 | 01 | 1 | INVR-01 | unit (AST audit) | `uv run pytest tests/matmaster/test_import_audit.py -x -q` | Update needed (W0) | ⬜ pending |
| 28-01-02 | 01 | 1 | INVR-02 | unit (AST audit) | `uv run pytest tests/matmaster/test_import_audit.py -x -q` | Update needed (W0) | ⬜ pending |
| 28-02-01 | 02 | 2 | CONS-03 | unit (import audit + behavior) | `uv run pytest tests/matmaster/integration/test_events_to_messages.py -x -q` | ✅ exists (17 tests baseline) | ⬜ pending |
| 28-02-02 | 02 | 2 | CONS-04 | unit (import audit) | `uv run pytest tests/matmaster/test_import_audit.py -x -q` | Update needed (W0) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/test_import_audit.py` — 更新规则：(1) 删除 `TestExpectedLazyBohrimImportsExist`（bohrium lazy imports 将不存在）；(2) 新增检测 matmaster/ 无 src lazy import 的规则；(3) 新增检测 matmaster/ 无 evomaster.agent.session lazy import 的规则
- [ ] `tests/matmaster/integration/test_events_to_messages.py` — 已存在 17 个测试，baseline 全部 pass。迁移后行为应保持一致
- [ ] 新测试：bohrium_setup 回调注入 unit test（mock callable，验证不触发 src import）
- [ ] 新测试：model_dump 输出格式断言（确认 tool_calls 使用 matmaster 扁平格式）

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| DB 历史事件 tool_calls 格式兼容 | CONS-03 | 需要真实 DB 数据验证两种格式 | 1. 查询包含 assistant_state 的历史事件 2. 确认 _adapt_tool_calls_format 能同时处理 nested 和 flat 格式 |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
