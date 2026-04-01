---
phase: 27
slug: mcp-calculation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-01
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
| 27-01-01 | 01 | 0 | MCP-01 | unit | `uv run pytest tests/matmaster/mcp/test_connection.py -x` | ❌ W0 | ⬜ pending |
| 27-01-02 | 01 | 0 | MCP-01 | unit | `uv run pytest tests/matmaster/mcp/test_manager.py -x` | ❌ W0 | ⬜ pending |
| 27-01-03 | 01 | 0 | MCP-01 | unit | `uv run pytest tests/matmaster/tools/test_lazy_mcp.py -x` | ✅ needs update | ⬜ pending |
| 27-01-04 | 01 | 0 | MCP-01 | unit | `uv run pytest tests/matmaster/tools/test_cache_mcp_schemas.py -x` | ❌ W0 | ⬜ pending |
| 27-02-01 | 02 | 0 | CALC-01 | unit | `uv run pytest tests/matmaster/adaptors/test_env_config.py -x` | ❌ W0 | ⬜ pending |
| 27-02-02 | 02 | 0 | CALC-01 | unit | `uv run pytest tests/matmaster/adaptors/test_path_adaptor.py -x` | ❌ W0 | ⬜ pending |
| 27-02-03 | 02 | 0 | CALC-01 | unit | `uv run pytest tests/matmaster/adaptors/test_job_service.py -x` | ❌ W0 | ⬜ pending |
| 27-02-04 | 02 | 0 | CALC-02 | unit | `uv run pytest tests/matmaster/adaptors/test_path_adaptor.py::test_resolve_args_compat -x` | ❌ W0 | ⬜ pending |
| 27-03-01 | 03 | 0 | ALL | audit | `uv run python -c "import ast, sys; ..."` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/mcp/__init__.py` + `test_connection.py` -- MCPConnection import 验证
- [ ] `tests/matmaster/mcp/test_manager.py` -- 精简版 MCPToolManager 单元测试
- [ ] `tests/matmaster/adaptors/__init__.py` + `calculation/__init__.py`
- [ ] `tests/matmaster/adaptors/calculation/test_env_config.py` -- resolve_mcp_config_path
- [ ] `tests/matmaster/adaptors/calculation/test_path_adaptor.py` -- resolve_args 兼容性
- [ ] `tests/matmaster/adaptors/calculation/test_job_service.py` -- job ID 解析
- [ ] `tests/matmaster/tools/test_lazy_mcp.py` -- 更新以适配新的直连架构
- [ ] Import audit script -- 验证 matmaster/ 不再 import evomaster.agent.tools.mcp 或 evomaster.adaptors.calculation

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Bohrium executor/storage/OSS 协议兼容 | CALC-02 | 需要实际 Bohrium 环境验证 | 在 Bohrium 环境提交 calculation job，验证 executor/storage/OSS 路径正确 |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
