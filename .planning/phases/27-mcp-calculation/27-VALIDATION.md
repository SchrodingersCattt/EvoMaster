---
phase: 27
slug: mcp-calculation
status: complete
nyquist_compliant: true
wave_0_complete: true
created: 2026-04-01
updated: 2026-04-01
---

# Phase 27 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2+ with pytest-asyncio |
| **Config file** | `pytest.ini` |
| **Quick run command** | `uv run pytest tests/matmaster/tools/test_lazy_mcp.py tests/matmaster/mcp/ tests/matmaster/adaptors/ -x` |
| **Full suite command** | `uv run pytest tests/matmaster/ -x` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/tools/test_lazy_mcp.py tests/matmaster/mcp/ tests/matmaster/adaptors/ -x`
- **After every plan wave:** Run `uv run pytest tests/matmaster/ -x`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 27-01-01 | 01 | 0 | MCP-01 | unit | `uv run pytest tests/matmaster/mcp/test_connection.py -x` | ✅ | ✅ green |
| 27-01-02 | 01 | 0 | MCP-01 | unit | `uv run pytest tests/matmaster/mcp/test_manager.py -x` | ✅ | ✅ green |
| 27-01-03 | 01 | 0 | MCP-01 | unit | `uv run pytest tests/matmaster/tools/test_lazy_mcp.py -x` | ✅ | ✅ green |
| 27-01-04 | 01 | 0 | MCP-01 | unit | `uv run pytest tests/matmaster/tools/test_cache_mcp_schemas.py -x` | ✅ | ✅ green |
| 27-02-01 | 02 | 0 | CALC-01 | unit | `uv run pytest tests/matmaster/adaptors/calculation/test_env_config.py -x` | ✅ | ✅ green |
| 27-02-02 | 02 | 0 | CALC-01 | unit | `uv run pytest tests/matmaster/adaptors/calculation/test_path_adaptor.py -x` | ✅ | ✅ green |
| 27-02-03 | 02 | 0 | CALC-01 | unit | `uv run pytest tests/matmaster/adaptors/calculation/test_job_service.py -x` | ✅ | ✅ green |
| 27-02-04 | 02 | 0 | CALC-02 | unit | `uv run pytest tests/matmaster/adaptors/calculation/test_path_adaptor.py::TestResolveArgsCompat -x` | ✅ | ✅ green |
| 27-03-01 | 03 | 0 | ALL | audit | `uv run pytest tests/matmaster/test_import_audit.py -x` | ✅ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `tests/matmaster/mcp/__init__.py` + `test_connection.py` -- MCPConnection import 验证
- [x] `tests/matmaster/mcp/test_manager.py` -- 精简版 MCPToolManager 单元测试
- [x] `tests/matmaster/adaptors/__init__.py` + `calculation/__init__.py`
- [x] `tests/matmaster/adaptors/calculation/test_env_config.py` -- resolve_mcp_config_path
- [x] `tests/matmaster/adaptors/calculation/test_path_adaptor.py` -- resolve_args 兼容性
- [x] `tests/matmaster/adaptors/calculation/test_job_service.py` -- job ID 解析
- [x] `tests/matmaster/tools/test_lazy_mcp.py` -- 适配新的直连架构（31 tests passing）
- [x] `tests/matmaster/test_import_audit.py` -- 验证 matmaster/ 不再 import evomaster.agent.tools.mcp 或 evomaster.adaptors.calculation

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Bohrium executor/storage/OSS 协议兼容 | CALC-02 | 需要实际 Bohrium 环境验证 | 在 Bohrium 环境提交 calculation job，验证 executor/storage/OSS 路径正确 |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** nyquist-auditor sign-off 2026-04-01

## Test Summary (nyquist-auditor run)

| Gap # | Task ID | File Created | Tests | Status | Debug Iterations |
|-------|---------|-------------|-------|--------|-----------------|
| 1 | 27-01-01 | `tests/matmaster/mcp/test_connection.py` | 20 | green | 0 |
| 2 | 27-01-02 | `tests/matmaster/mcp/test_manager.py` | 29 | green | 1 (docstring false positive) |
| 3 | 27-01-04 | `tests/matmaster/tools/test_cache_mcp_schemas.py` | 6 | green | 0 |
| 4 | 27-02-01 | `tests/matmaster/adaptors/calculation/test_env_config.py` | 11 | green | 0 |
| 5 | 27-02-02 | `tests/matmaster/adaptors/calculation/test_path_adaptor.py` | 14 | green | 0 |
| 6 | 27-02-03 | `tests/matmaster/adaptors/calculation/test_job_service.py` | 19 | green | 0 |
| 7 | 27-02-04 | (merged into test_path_adaptor.py) | 3 | green | 0 |
| 8 | 27-03-01 | `tests/matmaster/test_import_audit.py` | 13 | green | 1 (path calc error) |

**Total new tests: 115 passing**  
**Combined with existing test_lazy_mcp.py (31 tests): 153 tests green in 0.61s**
