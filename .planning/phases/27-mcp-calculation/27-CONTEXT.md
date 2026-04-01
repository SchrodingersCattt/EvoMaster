# Phase 27: MCP 与 Calculation 原生链路 - Context

**Gathered:** 2026-04-01
**Status:** Ready for planning

<domain>
## Phase Boundary

将 lazy_mcp、cache_mcp_schemas、eval_tooling_snapshot 和 exp.py 中对 `evomaster.agent.tools.mcp` 与 `evomaster.adaptors.calculation` 的全部依赖收回 matmaster 侧。同时将 monitor_job 中遗留的 `evomaster.adaptors.calculation.job_service` 依赖也一并收回。维持 Bohrium executor/storage/OSS 协议兼容。

不包含 `evomaster.env.bohrium` 的迁移（属 Phase 28 INVR 范围），path_adaptor 对其保留 lazy import。

</domain>

<decisions>
## Implementation Decisions

### MCP Manager 迁移策略
- **D-01:** 只搬入/重写 matmaster 实际使用的 MCPToolManager 功能，不带入 evomaster 冗余功能（reconnection runner、progress callback、registry 注册/反注册、reload_server 等不搬入）
- **D-02:** MCPConnection（stdio/sse/http 三种传输 + `create_connection` 工厂）搬入 matmaster，作为 MCP 连接的基础设施
- **D-03:** 重写精简版连接管理，保留 matmaster 实际需要的能力：`add_server`（连接 + list_tools）、`_build_tools`（含 dedup/filter/path_adaptor 注入）、`cleanup`、配置属性（path_adaptor_servers/factory、sync_tools_by_server、tool_include_only）

### Calculation 模块迁移范围
- **D-04:** env_config.py（resolve_mcp_config_path、get_current_env）搬入 matmaster，独立模块无外部依赖
- **D-05:** oss_io.py（upload_file_to_oss、download_oss_to_local）搬入 matmaster
- **D-06:** job_service.py（query_job_status、get_job_results、iterate_job_files、download_job_file、download_job_directory、terminate_job 等）搬入 matmaster，消除 monitor_job 的全部 evomaster.adaptors.calculation 依赖
- **D-07:** path_adaptor.py（CalculationPathAdaptor、get_calculation_path_adaptor）搬入 matmaster
- **D-08:** path_adaptor 对 `evomaster.env.bohrium`（get_bohrium_storage_config、inject_bohrium_executor）保留 lazy import。Phase 28 再处理 bohrium 函数迁移

### LazyMCPTool 执行架构
- **D-09:** 去掉 evomaster MCPTool 中间层，LazyMCPTool 直接调用 MCPConnection.call_tool（MCP SDK 原生接口），不再经过 MCPTool.execute(session, args_json) 的同步包装
- **D-10:** 由此 LazyMCPTool 不再需要 asyncio.to_thread 包装同步调用，可直接 await connection.call_tool

### Claude's Discretion
- path_adaptor 参数转换逻辑（resolve_args）在执行链路中的放置位置（LazyMCPTool.execute vs LazyMCPConnector）
- 搬入模块在 matmaster/ 内的具体目录组织（如 `matmaster/mcp/`、`matmaster/adaptors/calculation/` 等）
- cache_mcp_schemas.py 适配新原生 MCP Manager 的具体改法
- _build_tools 中 dedup/filter 逻辑的精简程度

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### MCP 子系统（迁移源）
- `evomaster/agent/tools/mcp/mcp_manager.py` — MCPToolManager 完整实现（连接管理、_build_tools、cleanup），需提取 matmaster 使用的子集
- `evomaster/agent/tools/mcp/mcp_connection.py` — MCPConnection ABC + stdio/sse/http 三种传输实现 + create_connection 工厂，需整体搬入
- `evomaster/agent/tools/mcp/mcp.py` — MCPTool（evomaster BaseTool 包装），**不搬入**但需理解其 path_adaptor resolve 逻辑以迁移到 LazyMCPTool

### Calculation 子系统（迁移源）
- `evomaster/adaptors/calculation/__init__.py` — 公开 API 列表，迁移范围参考
- `evomaster/adaptors/calculation/path_adaptor.py` — CalculationPathAdaptor（827 行），4 层 path 检测 + model alias + OSS upload + executor/storage 注入
- `evomaster/adaptors/calculation/env_config.py` — resolve_mcp_config_path（环境感知 MCP 配置路径）
- `evomaster/adaptors/calculation/oss_io.py` — upload_file_to_oss / download_oss_to_local（Aliyun OSS I/O）
- `evomaster/adaptors/calculation/job_service.py` — Bohrium OpenAPI 作业查询/下载（monitor_job 依赖）

### Bohrium 鉴权（Phase 28 迁移，本 phase 保留 lazy import）
- `evomaster/env/bohrium.py` — get_bohrium_storage_config / inject_bohrium_executor，path_adaptor 运行时依赖

### matmaster 当前依赖点（需修改的文件）
- `matmaster/tools/lazy_mcp.py` — LazyMCPTool + LazyMCPConnector + configure_mcp_manager，Phase 27 主改造目标
- `matmaster/tools/cache_mcp_schemas.py` — CLI 工具，依赖 MCPToolManager 连接 MCP server 生成 schema cache
- `matmaster/eval_tooling_snapshot.py` — 评估快照，依赖 resolve_mcp_config_path
- `matmaster/core/exp.py` §464-468 — lazy import resolve_mcp_config_path
- `matmaster/tools/builtin/monitor_job/_lifecycle.py` — lazy import job_service (get_job_results, query_job_status)
- `matmaster/tools/builtin/monitor_job/_llm.py` — lazy import job_service (terminate_job)
- `matmaster/tools/builtin/monitor_job/_logs.py` — lazy import job_service (iterate_job_files, get_file_token, download_job_file)
- `matmaster/tools/builtin/monitor_job/_download.py` — lazy import job_service (download_job_directory, get_file_token, iterate_job_files)

### 配置文件
- `matmaster_config/mcp.yaml` — MCP 运行时配置（path_adaptor、calculation_servers、calculation_executors）
- `matmaster_config/mcp_config.*.json` — MCP server 连接定义（mcpServers）

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `matmaster/tools/lazy_mcp.py`: LazyMCPTool 已实现 matmaster Tool Protocol，LazyMCPConnector 已有后台 event loop 管理。可在此基础上重构
- `matmaster/tools/lazy_mcp.py:configure_mcp_manager()`: 已抽出的 MCP 域配置注入函数，可复用到新的 matmaster 原生 MCP Manager
- `matmaster/tools/builtin/monitor_job/`: Phase 26 已搬入 matmaster，结构完整，只需改 import 路径

### Established Patterns
- Lazy import 策略：matmaster 全项目使用函数级 lazy import 延迟加载外部模块（Phase 26 确立）
- Tool Protocol：`name`, `description`, `json_schema`, `execute(arguments) -> ToolResult`
- BuiltinTool ABC：`self.session` 注入、`_execute → execute` 异步包装

### Integration Points
- `LazyMCPConnector` 是 MCP 子系统与 matmaster Exp 层的唯一桥梁（在 `exp.py._init_skill_tools` 中创建）
- `configure_mcp_manager()` 注入 path_adaptor_factory、sync_tools、tool_include_only — 迁移后这些配置注入需对接新的原生 MCP Manager
- `schema_cache.load()` / `schema_cache.save()` 在 `matmaster/cache/` 目录操作，与 MCPToolManager 无关可直接复用

</code_context>

<specifics>
## Specific Ideas

- 用户明确要求"只搬入 matmaster 实际使用的功能，不带入 evomaster 冗余功能"
- 与 Phase 25/26 "最小成本断依赖"策略一脉相承，但本 phase 更偏向"重写精简版"而非"原样搬入"
- path_adaptor 对 evomaster.env.bohrium 的 lazy import 是有意留给 Phase 28 的过渡状态

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 27-mcp-calculation*
*Context gathered: 2026-04-01*
