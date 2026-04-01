# Phase 27: MCP 与 Calculation 原生链路 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-01
**Phase:** 27-mcp-calculation
**Areas discussed:** MCP Manager 迁移策略, Calculation 依赖链与边界, LazyMCPTool 执行架构

---

## MCP Manager 迁移策略

| Option | Description | Selected |
|--------|-------------|----------|
| 完整搬入 MCPToolManager | 把 MCPToolManager + MCPConnection + MCPTool 整体搬入，保留重连、runner lifecycle、progress callback 等完整能力 | |
| 最小搬入 + 简化 | 只搬 MCPConnection + 必要的 _build_tools 逻辑，LazyMCPConnector 直接用 MCPConnection 替代 MCPToolManager | |
| 先搬后简化（两步走） | 先完整搬入断依赖，后续里程碑再重构简化 | |

**User's choice:** 只搬入/重写所有 matmaster 中使用到的功能，不要将 evomaster 的冗余功能带入 matmaster
**Notes:** 用户明确拒绝将 evomaster 的冗余功能（reconnection runner、progress callback、registry 注册/反注册、reload_server）带入 matmaster。原则是只搬入 matmaster 实际使用的功能子集。

---

## Calculation 依赖链与边界

| Option | Description | Selected |
|--------|-------------|----------|
| 全部搬入 + 常量内化 | 把 path_adaptor/env_config/oss_io/job_service + bohrium.py 全部搬入，BOHRIUM_OPENAPI_HOST 直接从 os.getenv 读 | |
| 搬入 + 回调注入 bohrium | 搬入 calculation 模块但 bohrium storage/executor 通过回调注入 | |
| 只搬必要部分 | 只搬 env_config + oss_io + job_service，path_adaptor 保留对 evomaster.env.bohrium 的 lazy import | ✓ |

**User's choice:** 只搬必要部分
**Notes:** 初始选择是只搬必要部分。后续追问确认 path_adaptor 也需要搬入（因为 SC2 要求），但 bohrium 函数保留 lazy import。

### Follow-up: path_adaptor 中 bohrium 函数处理

| Option | Description | Selected |
|--------|-------------|----------|
| 一起搬 + 常量内化 | get_bohrium_storage_config / inject_bohrium_executor 一并搬入，BOHRIUM_OPENAPI_HOST 直接从 os.getenv 读 | |
| 回调注入 | path_adaptor 不直接调这两个函数，改为通过构造时注入的 callable | |
| 保留 lazy import | 搬 path_adaptor 但保留对 evomaster.env.bohrium 的 lazy import，Phase 28 再处理 | ✓ |

**User's choice:** 保留 lazy import
**Notes:** bohrium.py 本身依赖 src.utils.constant.BOHRIUM_OPENAPI_HOST，属于 Phase 28 (INVR-02) 范围。Phase 27 不提前处理。

---

## LazyMCPTool 执行架构

| Option | Description | Selected |
|--------|-------------|----------|
| 直连 MCP SDK | LazyMCPTool 直接持有 MCPConnection，调 connection.call_tool，去掉 MCPTool 中间层 | ✓ |
| 保持当前结构 | 搬入 MCPTool 类，保持 LazyMCPTool → MCPTool.execute 链路 | |

**User's choice:** 直连 MCP SDK
**Notes:** 去掉 MCPTool 中间层后链路更短，也不需要 asyncio.to_thread 包装同步接口。

### Follow-up: path_adaptor resolve 放置位置

| Option | Description | Selected |
|--------|-------------|----------|
| LazyMCPTool.execute 内 | 在 LazyMCPTool.execute 中调 path_adaptor.resolve_args | |
| LazyMCPConnector 内 | 在 connector 层做 path resolve | |
| Claude 决定 | 由 Claude 根据代码结构判断最合适的位置 | ✓ |

**User's choice:** 你来决定
**Notes:** 用户将此技术细节委托给 Claude。

---

## Claude's Discretion

- path_adaptor resolve 在执行链路中的放置位置
- 搬入模块在 matmaster/ 内的具体目录组织
- cache_mcp_schemas.py 适配新原生 MCP Manager 的具体改法
- _build_tools 中 dedup/filter 逻辑的精简程度

## Deferred Ideas

None — discussion stayed within phase scope
