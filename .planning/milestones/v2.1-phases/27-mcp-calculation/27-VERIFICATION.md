---
phase: 27-mcp-calculation
verified: 2026-04-01T12:00:00Z
status: passed
score: 7/7 must-haves verified
re_verification: false
---

# Phase 27: MCP 与 Calculation 原生链路 Verification Report

**Phase Goal:** MCP 连接、schema cache 与 calculation path adaptor 全部收回 matmaster 侧，同时维持 Bohrium executor/storage/OSS 协议兼容
**Verified:** 2026-04-01
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | matmaster.tools.lazy_mcp 可独立连接 MCP server，不依赖 evomaster.agent.tools.mcp | VERIFIED | lazy_mcp.py 无 evomaster import；从 matmaster.mcp.manager 导入 MCPToolManager；31 测试通过 |
| 2 | matmaster 侧可原生解析 calculation runtime config、path adaptor 与 schema cache | VERIFIED | matmaster/adaptors/calculation/ 包含 env_config/oss_io/job_service/path_adaptor，模块级 import 不触发 evomaster 加载 |
| 3 | Bohrium executor/storage/OSS 协议兼容保留 | VERIFIED | path_adaptor.py 函数体内 lazy import `evomaster.env.bohrium`（inject_bohrium_executor, get_bohrium_storage_config）；job_service.py 函数体内 lazy import get_bohrium_credentials |
| 4 | cache_mcp_schemas.py 和 eval_tooling_snapshot.py 不再 import evomaster MCP/calculation | VERIFIED | cache_mcp_schemas.py: `from matmaster.mcp.manager import MCPToolManager`；eval_tooling_snapshot.py: `from matmaster.adaptors.calculation import resolve_mcp_config_path` |
| 5 | LazyMCPTool.execute 直接 await MCPConnection.call_tool，不经过 MCPTool 中间层 | VERIFIED | lazy_mcp.py 第 83 行 `await self._connection.call_tool()`；无 asyncio.to_thread；无 MCPTool 类 |
| 6 | monitor_job 全部 4 个子模块从 matmaster.adaptors.calculation 获取 job_service | VERIFIED | _lifecycle.py/_llm.py/_logs.py/_download.py 全部使用 `from matmaster.adaptors.calculation.job_service import` |
| 7 | matmaster/ 运行时路径不再 import evomaster.agent.tools.mcp 或 evomaster.adaptors.calculation | VERIFIED | `grep -rn "from evomaster.agent.tools.mcp\|from evomaster.adaptors.calculation" matmaster/` 返回空 |

**Score:** 7/7 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `matmaster/mcp/__init__.py` | MCP 子系统公开 API | VERIFIED | 9 行；导出 MCPConnection/create_connection/MCPToolManager |
| `matmaster/mcp/connection.py` | MCPConnection ABC + 3 种传输 + create_connection | VERIFIED | 227 行 (min_lines: 200)；MCPConnectionStdio/SSE/HTTP + 工厂函数；零 evomaster |
| `matmaster/mcp/manager.py` | 精简版 MCPToolManager | VERIFIED | 315 行 (min_lines: 100)；add_server/_build_tools/cleanup；无 reconnect/progress/register_tools/MCPTool 实例 |
| `matmaster/adaptors/calculation/__init__.py` | Calculation 子系统公开 API | VERIFIED | 43 行；包含 resolve_mcp_config_path 等 15 个导出 |
| `matmaster/adaptors/calculation/env_config.py` | MCP 配置路径解析 + 环境检测 | VERIFIED | 74 行 (min_lines: 60)；零 evomaster |
| `matmaster/adaptors/calculation/oss_io.py` | Aliyun OSS 文件上传/下载 | VERIFIED | 139 行 (min_lines: 100)；零 evomaster |
| `matmaster/adaptors/calculation/job_service.py` | Bohrium OpenAPI 作业查询/下载 | VERIFIED | 680 行 (min_lines: 600)；get_bohrium_credentials 仅函数体内 lazy import |
| `matmaster/adaptors/calculation/path_adaptor.py` | CalculationPathAdaptor + get_calculation_path_adaptor | VERIFIED | 849 行 (min_lines: 800)；inject_bohrium_executor/get_bohrium_storage_config 仅函数体内 lazy import |
| `matmaster/tools/lazy_mcp.py` | LazyMCPTool 直连 + LazyMCPConnector 使用原生 manager | VERIFIED | 283 行；`from matmaster.mcp` + `from matmaster.adaptors.calculation`；含 ensure_connection/_format_result |
| `matmaster/tools/cache_mcp_schemas.py` | 使用原生 MCPToolManager 的 schema cache CLI | VERIFIED | 114 行；`from matmaster.mcp.manager import MCPToolManager`；dict-based tools_by_server 访问 |
| `matmaster/core/exp.py` | resolve_mcp_config_path 从 matmaster 导入 | VERIFIED | 第 466 行 `from matmaster.adaptors.calculation import resolve_mcp_config_path` |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| matmaster/mcp/manager.py | matmaster/mcp/connection.py | `from .connection import create_connection` | WIRED | 第 15 行确认 |
| matmaster/tools/lazy_mcp.py:LazyMCPTool.execute | matmaster/mcp/connection.py:MCPConnection.call_tool | `await self._connection.call_tool(` | WIRED | 第 83 行直接调用 |
| matmaster/tools/lazy_mcp.py:LazyMCPConnector | matmaster/mcp/manager.py:MCPToolManager | `from matmaster.mcp.manager import MCPToolManager` | WIRED | 第 207 行；ensure_manager 内 |
| matmaster/tools/lazy_mcp.py:configure_mcp_manager | matmaster/adaptors/calculation/path_adaptor.py | `from matmaster.adaptors.calculation import get_calculation_path_adaptor` | WIRED | 第 146 行 lazy import |
| matmaster/tools/builtin/monitor_job/ | matmaster/adaptors/calculation/job_service.py | `from matmaster.adaptors.calculation.job_service import` | WIRED | _lifecycle/_llm/_logs/_download 均确认 |
| matmaster/adaptors/calculation/path_adaptor.py | matmaster/adaptors/calculation/oss_io.py | `from .oss_io import upload_file_to_oss` | WIRED | 第 41 行确认 |
| matmaster/adaptors/calculation/path_adaptor.py | evomaster.env.bohrium (保留 per D-08) | 函数体内 lazy import | WIRED | 第 521 行 inject_bohrium_executor；第 638 行 get_bohrium_storage_config |
| matmaster/adaptors/calculation/job_service.py | matmaster/adaptors/calculation/env_config.py | `from .env_config import get_current_env` | WIRED | 第 29 行确认 |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|--------------------|--------|
| cache_mcp_schemas.py | tools_by_server | MCPToolManager.add_server + _build_tools | 是，MCPToolManager 从 list_tools 填充 | FLOWING |
| lazy_mcp.py:LazyMCPTool.execute | result_content | MCPConnection.call_tool (MCP SDK) | 是，直接调用 MCP server session | FLOWING |
| lazy_mcp.py:configure_mcp_manager | path_adaptor_factory | get_calculation_path_adaptor(mcp_config) | 是，工厂函数返回真实 CalculationPathAdaptor | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| matmaster.mcp 包级 import | `python -c "from matmaster.mcp import MCPConnection, create_connection, MCPToolManager"` | OK | PASS |
| adaptors.calculation 包级 import 不触发 evomaster | 清空 evomaster 模块后 import matmaster.adaptors.calculation | NONE loaded | PASS |
| matmaster.mcp + lazy_mcp 不触发 evomaster | 清空后 import matmaster.mcp, matmaster.tools.lazy_mcp | NONE loaded | PASS |
| test_lazy_mcp.py 全部通过 | `uv run pytest tests/matmaster/tools/test_lazy_mcp.py -x` | 31 passed in 0.26s | PASS |
| 无 evomaster.agent.tools.mcp 或 evomaster.adaptors.calculation 残留 | `grep -rn "from evomaster.agent.tools.mcp\|from evomaster.adaptors.calculation" matmaster/` | 空输出 | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| MCP-01 | 27-01, 27-03 | 开发者可以通过 matmaster.tools.lazy_mcp 连接 MCP server、缓存 schema 并执行 tool，而不依赖 evomaster.agent.tools.mcp.* | SATISFIED | matmaster/mcp/ 包创建完毕；lazy_mcp.py 直连 MCPConnection；cache_mcp_schemas 使用原生 manager；31 测试通过 |
| CALC-01 | 27-02, 27-03 | 开发者可以在 matmaster 侧解析 calculation runtime config、path adaptor 与 schema cache，而不直接导入 evomaster.adaptors.calculation.* | SATISFIED | matmaster/adaptors/calculation/ 包完整实现；exp.py/eval_tooling_snapshot/cache_mcp_schemas 全部切换到 matmaster 路径 |
| CALC-02 | 27-02, 27-03 | 解耦后 Bohrium / calculation tool 的 executor、storage、OSS 上传与远端路径适配行为保持与当前协议兼容 | SATISFIED | path_adaptor.py 保留 inject_bohrium_executor + get_bohrium_storage_config 调用（函数体内 lazy import per D-08）；resolve_args 4 层 path 检测逻辑完整保留（849 行） |

**备注：** REQUIREMENTS.md Traceability 表显示三个需求均已标记为 Complete，与验证结果一致。无 orphaned requirements（Phase 27 仅声明 MCP-01/CALC-01/CALC-02 三个，全部被覆盖）。

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| matmaster/adaptors/calculation/job_service.py | 4-5 (docstring) | 注释中仍引用 `evomaster/agent/tools/builtin/monitor_job/` 旧路径 | Info | 仅文档字符串，无运行时影响 |
| matmaster/tools/builtin/monitor_job/_llm.py | 67-68 | `from evomaster.config import ConfigManager` + `from evomaster.utils import LLMConfig, create_llm` | Info | 不在本 Phase 范围内（非 evomaster.agent.tools.mcp 或 evomaster.adaptors.calculation）；属于 Phase 28 的待清理项 |

**说明：** _llm.py 中的 evomaster.config/evomaster.utils import 是合法的范围外残留，Phase 27 目标仅涵盖消除 `evomaster.agent.tools.mcp` 和 `evomaster.adaptors.calculation` 两个命名空间的 import，PLAN 03 acceptance criteria 明确界定了这一范围（`grep -rn "from evomaster.agent.tools.mcp\|from evomaster.adaptors.calculation" matmaster/`）。

### Human Verification Required

无需人工验证。Bohrium 协议兼容性（executor/storage/OSS 参数生成）在运行时依赖真实 Bohrium 凭证，但代码路径已验证 lazy import 存在且位置正确，逻辑未被修改（仅 import 路径，business logic 完整保留）。

### Gaps Summary

无 gap。所有 must-have truths 均通过 Level 1-4 验证：
- 所有 11 个 artifact 存在且行数超过最低要求
- 所有关键 key_links 均有代码证据
- data-flow 完整：MCP server -> call_tool -> _format_result -> ToolResult
- 31 个测试全部通过
- 模块级 import 不触发 evomaster 加载（运行时验证通过）
- `from evomaster.agent.tools.mcp` 和 `from evomaster.adaptors.calculation` 在 matmaster/ 下完全消除

---

_Verified: 2026-04-01_
_Verifier: Claude (gsd-verifier)_
