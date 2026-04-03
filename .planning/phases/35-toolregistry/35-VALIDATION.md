---
phase: 35
slug: toolregistry
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-03
---

# Phase 35 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (via uv run) |
| **Config file** | pyproject.toml |
| **Quick run command** | `uv run pytest tests/matmaster/tools/test_write_tool.py tests/matmaster/tools/test_edit_tool.py tests/matmaster/tools/test_bash_tool.py tests/matmaster/core/test_guard_pipeline.py tests/matmaster/core/test_capability_policy.py tests/matmaster/core/test_context_builder.py -q` |
| **Full suite command** | `uv run pytest tests/matmaster/ -q` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/tools/test_write_tool.py tests/matmaster/tools/test_edit_tool.py tests/matmaster/tools/test_bash_tool.py tests/matmaster/core/test_guard_pipeline.py tests/matmaster/core/test_capability_policy.py tests/matmaster/core/test_context_builder.py -q`
- **After every plan wave:** Run `uv run pytest tests/matmaster/ -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 35-01-01 | 01 | 0 | CMIG-01 | unit | `uv run pytest tests/matmaster/core/test_guard_pipeline.py -x -k "read_before_modify"` | ❌ W0 | ⬜ pending |
| 35-01-02 | 01 | 0 | CMIG-02 | unit | `uv run pytest tests/matmaster/core/test_capability_policy.py -x -k "bash_safety"` | ❌ W0 | ⬜ pending |
| 35-01-03 | 01 | 0 | CMIG-03 | unit | `uv run pytest tests/matmaster/tools/test_tool_compiler.py -x -k "stop_mode"` | ❌ W0 | ⬜ pending |
| 35-01-04 | 01 | 0 | CMIG-03 | unit | `uv run pytest tests/matmaster/core/test_tool_scheduler.py -x -k "stop_mode"` | ❌ W0 | ⬜ pending |
| 35-02-01 | 02 | 1 | CMIG-01 | unit | `uv run pytest tests/matmaster/tools/test_write_tool.py -x` | ✅ (needs update) | ⬜ pending |
| 35-02-02 | 02 | 1 | CMIG-01 | unit | `uv run pytest tests/matmaster/tools/test_edit_tool.py -x` | ✅ (needs update) | ⬜ pending |
| 35-03-01 | 03 | 1 | CMIG-02 | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py -x` | ✅ (needs update) | ⬜ pending |
| 35-04-01 | 04 | 2 | CMIG-04 | unit | `uv run pytest tests/matmaster/tools/ -x -k "registry"` | ✅ (needs update) | ⬜ pending |
| 35-04-02 | 04 | 2 | CMIG-04 | integration | `uv run pytest tests/matmaster/core/test_tool_runner.py -x` | ✅ | ⬜ pending |
| 35-05-01 | 05 | 2 | CMIG-05 | unit | `uv run pytest tests/matmaster/core/test_context_builder.py -x` | ✅ (needs update) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/core/test_guard_pipeline.py` — 新增 ReadBeforeModifyGuard 测试用例
- [ ] `tests/matmaster/core/test_capability_policy.py` — 新增 bash_safety 测试用例 + 修复 2 个预存在失败
- [ ] `tests/matmaster/tools/test_tool_compiler.py` — 新增 state_mode/stop_mode 填充验证
- [ ] `tests/matmaster/core/test_tool_scheduler.py` — 新增 stop_mode 消费测试
- [ ] 现有工具测试（write_tool/edit_tool/bash_tool）需要更新，反映安全检查已迁出

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| ToolRegistry 方法实际删除 | CMIG-04 | 静态验证 | grep -r "registry.execute\|registry.get_tool_definitions" matmaster/ 返回空 |
| agent.py legacy 路径删除 | CMIG-04 | 静态验证 | grep -n "tool_registry" matmaster/core/agent.py 只保留 deprecated 注释 |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
