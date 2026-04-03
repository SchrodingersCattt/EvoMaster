---
phase: 35
slug: toolregistry
status: validated
nyquist_compliant: true
wave_0_complete: true
created: 2026-04-03
validated: 2026-04-03
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
| 35-01-01 | 01 | 1 | CMIG-01 | unit | `uv run pytest tests/matmaster/core/test_guard_pipeline.py -x -k "read_before_modify"` | ✅ | ✅ green |
| 35-01-02 | 01 | 1 | CMIG-02 | unit | `uv run pytest tests/matmaster/core/test_capability_policy.py -x -k "bash_safety"` | ✅ | ✅ green |
| 35-01-03 | 02 | 1 | CMIG-03 | unit | `uv run pytest tests/matmaster/tools/test_tool_compiler.py -x -k "stop_mode"` | ✅ | ✅ green |
| 35-01-04 | 02 | 1 | CMIG-03 | unit | `uv run pytest tests/matmaster/core/test_tool_runner.py -x -k "cancel"` | ✅ | ✅ green |
| 35-02-01 | 01 | 1 | CMIG-01 | unit | `uv run pytest tests/matmaster/tools/test_write_tool.py -x` | ✅ | ✅ green |
| 35-02-02 | 01 | 1 | CMIG-01 | unit | `uv run pytest tests/matmaster/tools/test_edit_tool.py -x` | ✅ | ✅ green |
| 35-03-01 | 01 | 1 | CMIG-02 | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py -x` | ✅ | ✅ green |
| 35-04-01 | 03 | 2 | CMIG-04 | unit | `uv run pytest tests/matmaster/tools/ -x -k "registry"` | ✅ | ✅ green |
| 35-04-02 | 03 | 2 | CMIG-04 | integration | `uv run pytest tests/matmaster/core/test_tool_runner.py -x` | ✅ | ✅ green |
| 35-05-01 | 03 | 2 | CMIG-05 | unit | `uv run pytest tests/matmaster/core/test_context_builder.py -x` | ✅ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `tests/matmaster/core/test_guard_pipeline.py` — 6 ReadBeforeModifyGuard 测试用例
- [x] `tests/matmaster/core/test_capability_policy.py` — 5 bash_safety 测试用例
- [x] `tests/matmaster/tools/test_tool_compiler.py` — 7 state_mode/stop_mode 填充验证
- [x] `tests/matmaster/core/test_tool_runner.py` — 4 stop_mode cancel 行为测试（原计划放 test_tool_scheduler，实际放 test_tool_runner 更合理）
- [x] 现有工具测试（write_tool/edit_tool/bash_tool）已更新，反映安全检查已迁出

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| ToolRegistry 方法实际删除 | CMIG-04 | 静态验证 | grep -r "registry.execute\|registry.get_tool_definitions" matmaster/ 返回空 |
| agent.py legacy 路径删除 | CMIG-04 | 静态验证 | grep -n "tool_registry" matmaster/core/agent.py 只保留 deprecated 注释 |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** validated

---

## Validation Audit 2026-04-03

| Metric | Count |
|--------|-------|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |

**Evidence:**
- 136 tests pass across all phase 35 modules (`uv run pytest ... -q` exit 0)
- All 10 validation tasks have automated test coverage
- Task 35-01-04 relocated from test_tool_scheduler.py to test_tool_runner.py (FullToolRunner owns cancel strategy, architecturally correct)
