# Phase 27: MCP 与 Calculation 原生链路 - Research

**Researched:** 2026-04-01
**Domain:** MCP 连接管理、Calculation path adaptor、Bohrium job service -- 从 evomaster 迁移到 matmaster
**Confidence:** HIGH

## Summary

Phase 27 的核心任务是将 matmaster 对 evomaster MCP 子系统（mcp_manager、mcp_connection）和 calculation 子系统（path_adaptor、env_config、oss_io、job_service）的全部运行时依赖收回 matmaster 侧。这是一个迁移+精简的 phase，不涉及新协议或新功能，但涉及大量文件的 import 路径修改和一个关键的架构重写（LazyMCPTool 不再经过 MCPTool 中间层，直接调用 MCPConnection.call_tool）。

迁移范围明确：6 个 evomaster 源文件需要搬入/重写，10+ 个 matmaster 文件需要修改 import 路径。关键风险在于 path_adaptor.py（827 行，4 层 path 检测 + model alias + OSS upload + executor/storage 注入）的正确搬入，以及 LazyMCPTool 执行链路的架构变更。path_adaptor 对 `evomaster.env.bohrium`（get_bohrium_storage_config、inject_bohrium_executor）的依赖按 D-08 决定保留为 lazy import，留给 Phase 28 处理。

**Primary recommendation:** 按 MCP 连接层 -> Calculation 模块 -> LazyMCPTool 执行链路重写 -> import 路径批量修改 的顺序执行，每个阶段可独立验证。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** 只搬入/重写 matmaster 实际使用的 MCPToolManager 功能，不带入 evomaster 冗余功能（reconnection runner、progress callback、registry 注册/反注册、reload_server 等不搬入）
- **D-02:** MCPConnection（stdio/sse/http 三种传输 + `create_connection` 工厂）搬入 matmaster，作为 MCP 连接的基础设施
- **D-03:** 重写精简版连接管理，保留 matmaster 实际需要的能力：`add_server`（连接 + list_tools）、`_build_tools`（含 dedup/filter/path_adaptor 注入）、`cleanup`、配置属性（path_adaptor_servers/factory、sync_tools_by_server、tool_include_only）
- **D-04:** env_config.py（resolve_mcp_config_path、get_current_env）搬入 matmaster，独立模块无外部依赖
- **D-05:** oss_io.py（upload_file_to_oss、download_oss_to_local）搬入 matmaster
- **D-06:** job_service.py（query_job_status、get_job_results、iterate_job_files、download_job_file、download_job_directory、terminate_job 等）搬入 matmaster，消除 monitor_job 的全部 evomaster.adaptors.calculation 依赖
- **D-07:** path_adaptor.py（CalculationPathAdaptor、get_calculation_path_adaptor）搬入 matmaster
- **D-08:** path_adaptor 对 `evomaster.env.bohrium`（get_bohrium_storage_config、inject_bohrium_executor）保留 lazy import。Phase 28 再处理 bohrium 函数迁移
- **D-09:** 去掉 evomaster MCPTool 中间层，LazyMCPTool 直接调用 MCPConnection.call_tool（MCP SDK 原生接口），不再经过 MCPTool.execute(session, args_json) 的同步包装
- **D-10:** 由此 LazyMCPTool 不再需要 asyncio.to_thread 包装同步调用，可直接 await connection.call_tool

### Claude's Discretion
- path_adaptor 参数转换逻辑（resolve_args）在执行链路中的放置位置（LazyMCPTool.execute vs LazyMCPConnector）
- 搬入模块在 matmaster/ 内的具体目录组织（如 `matmaster/mcp/`、`matmaster/adaptors/calculation/` 等）
- cache_mcp_schemas.py 适配新原生 MCP Manager 的具体改法
- _build_tools 中 dedup/filter 逻辑的精简程度

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| MCP-01 | matmaster.tools.lazy_mcp 可以独立连接 MCP server、缓存 schema 并执行 tool，不依赖 evomaster.agent.tools.mcp.* | MCPConnection 整体搬入 + 精简版 MCPToolManager 重写 + LazyMCPTool 直连 call_tool |
| CALC-01 | matmaster 侧可以原生解析 calculation runtime config、path adaptor 与 schema cache，不直接导入 evomaster.adaptors.calculation.* | env_config/oss_io/path_adaptor/job_service 四模块搬入 matmaster |
| CALC-02 | Bohrium/calculation tool 的 executor、storage、OSS 上传与远端路径适配行为保持与当前协议兼容 | path_adaptor 原样保留全部 resolve_args 逻辑；bohrium 函数通过 lazy import 继续调用 |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| mcp | 1.26.0 | MCP SDK -- ClientSession, stdio/sse/http client | 项目已安装；MCPConnection 直接依赖 |
| anyio | 4.12.1 | MCP SDK 的 async I/O 基础 | mcp 的传递依赖 |
| httpx | 0.28.1 | SSE/HTTP MCP 传输层使用 | mcp 的传递依赖 |
| oss2 | 2.19.1 | Aliyun OSS 文件上传/下载 | oss_io.py 使用，已安装 |
| pyyaml | - | mcp.yaml 配置解析 | cache_mcp_schemas 使用 |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | 9.0.2+ | 测试框架 | asyncio_mode=auto |
| pytest-asyncio | - | async test support | 已配置 asyncio_mode=auto |

**No new dependencies needed.** 所有迁移模块使用的库已在项目中安装。

## Architecture Patterns

### Recommended Module Organization

基于 discretion area，推荐以下目录结构：

```
matmaster/
├── mcp/                           # MCP 子系统（新建）
│   ├── __init__.py                # 公开 API
│   ├── connection.py              # MCPConnection ABC + stdio/sse/http + create_connection
│   └── manager.py                 # 精简版 MCPToolManager
├── adaptors/                      # 外部服务适配器（新建）
│   └── calculation/               # Bohrium calculation 适配
│       ├── __init__.py            # 公开 API
│       ├── env_config.py          # resolve_mcp_config_path, get_current_env
│       ├── oss_io.py              # upload_file_to_oss, download_oss_to_local
│       ├── path_adaptor.py        # CalculationPathAdaptor, get_calculation_path_adaptor
│       └── job_service.py         # Bohrium OpenAPI job status/download
├── tools/
│   ├── lazy_mcp.py                # 修改：LazyMCPTool 直连 + LazyMCPConnector 使用原生 manager
│   ├── cache_mcp_schemas.py       # 修改：使用 matmaster.mcp.manager
│   └── builtin/monitor_job/       # 修改：import 路径改为 matmaster.adaptors.calculation
└── ...
```

**推荐理由:**
1. `matmaster/mcp/` -- 镜像 evomaster 的 `agent/tools/mcp/` 但只保留连接管理，不含 MCPTool 包装层。命名清晰，与 MCP SDK 对齐
2. `matmaster/adaptors/calculation/` -- 保持与 evomaster 相同的目录名和模块名，降低搬入成本和认知负担。后续其他 adaptor（如果有的话）可放在 `adaptors/` 下

### Pattern 1: MCPConnection 整体搬入（D-02）

MCPConnection 是一个自包含的 ABC + 3 个子类 + 工厂函数，共 222 行。无 evomaster 内部依赖，仅依赖 `mcp` SDK。

**搬入方式:** 原样复制到 `matmaster/mcp/connection.py`，无需修改。

```python
# matmaster/mcp/connection.py
# 内容与 evomaster/agent/tools/mcp/mcp_connection.py 完全一致
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

class MCPConnection(ABC): ...
class MCPConnectionStdio(MCPConnection): ...
class MCPConnectionSSE(MCPConnection): ...
class MCPConnectionHTTP(MCPConnection): ...
def create_connection(...) -> MCPConnection: ...
```

### Pattern 2: 精简版 MCPToolManager（D-01, D-03）

当前 evomaster MCPToolManager（672 行）包含大量 matmaster 不使用的功能。精简版应保留：

**保留的能力（~200 行估算）:**
- `__init__`: connections、tools_by_server、配置属性（path_adaptor_servers、path_adaptor_factory、tool_include_only、sync_tools_by_server）
- `add_server`: 简化版 -- 连接 + list_tools + _build_tools，不含 runner task / reconnection / progress callback
- `_build_tools`: 完整保留（include_only filter + sync_tools filter + dedup + path_adaptor 注入）-- 这是 schema cache 生成的核心逻辑
- `cleanup`: 简化版 -- 遍历 connections 调用 __aexit__
- `loop` 属性

**不搬入的功能:**
- `_server_tasks` / `_server_stop` / `_server_ready` (runner task 管理)
- `_reconnect_events` / `_reconnect_waiters` / `request_reconnect` / `_notify_reconnect_waiters` (重连机制)
- `_progress_callback` / `set_progress_callback` / `_emit_progress` (进度回调)
- `_registered_registry` / `register_tools` / `register_tools_into` (ToolRegistry 注册)
- `remove_server` / `reload_server` (热更新)
- `_update_tool_connections` (重连后更新引用)
- `get_stats` / `get_tool_names` / `get_server_names` / `get_tools_by_server` (统计/查询)

**add_server 简化思路:**
```python
async def add_server(self, name: str, transport: str, **connection_kwargs) -> None:
    """连接 MCP server、获取 tools、构建 tool 对象。不含 runner/reconnection。"""
    conn_ctx = create_connection(transport=transport, **connection_kwargs)
    conn = await asyncio.wait_for(conn_ctx.__aenter__(), timeout=MCP_CONNECT_TIMEOUT)
    try:
        tools_info = await asyncio.wait_for(conn.list_tools(), timeout=MCP_CONNECT_TIMEOUT)
    except Exception:
        await conn_ctx.__aexit__(None, None, None)
        raise
    self.connections[name] = conn
    self._conn_ctxs[name] = conn_ctx  # 保留上下文用于 cleanup
    self._build_tools(name, conn, tools_info)
```

### Pattern 3: LazyMCPTool 直连 MCPConnection.call_tool（D-09, D-10）

当前链路: `LazyMCPTool.execute` -> `asyncio.to_thread(MCPTool.execute)` -> `MCPTool._call_mcp_tool_sync` -> `asyncio.run_coroutine_threadsafe(connection.call_tool)`

新链路: `LazyMCPTool.execute` -> `await connection.call_tool(remote_tool_name, arguments)`

**关键变化:**
1. `LazyMCPTool._real_tool: MCPTool` 变为 `LazyMCPTool._connection: MCPConnection`
2. `execute` 从同步桥接变为原生 async
3. path_adaptor 的 resolve_args 调用移入 LazyMCPTool.execute（推荐放在 execute 中而非 connector 中，因为 tool 持有 description 和 schema 信息）

```python
async def execute(self, arguments: dict[str, Any]) -> ToolResult:
    if self._connection is None:
        self._connection = await self._connector.ensure_connection(self._server_name)

    # path_adaptor resolve (if configured)
    resolved_args = self._resolve_path_args(arguments) if self._path_adaptor else arguments

    try:
        result = await self._connection.call_tool(self._remote_tool_name, resolved_args)
        content = self._format_result(result)
        return ToolResult(status="success", content=content)
    except RuntimeError as e:
        # MCPConnection.call_tool raises RuntimeError on isError=True
        return ToolResult(status="error", content=str(e))
```

### Pattern 4: path_adaptor resolve_args 的放置位置（Discretion）

**推荐: 放在 LazyMCPTool.execute 中**

理由:
1. resolve_args 需要 tool_description、input_schema、server_name -- 这些信息 LazyMCPTool 已持有
2. 需要 workspace_path 和 bohrium_credentials -- 这些可通过 connector 的 session 获取
3. 保持 LazyMCPConnector 的职责单一（只管连接管理）
4. 与 D-09 的直连设计一致 -- LazyMCPTool 负责完整的 execute 流程

### Pattern 5: Calculation 模块搬入策略

| 模块 | 行数 | 外部依赖 | 搬入策略 |
|------|------|----------|----------|
| env_config.py | 75 | 无（仅 os/pathlib/logging） | 原样搬入 |
| oss_io.py | 140 | oss2（lazy import） | 原样搬入 |
| job_service.py | 682 | env_config.get_current_env + evomaster.env.get_bohrium_credentials | 搬入，改 `from .env_config import get_current_env`；`get_bohrium_credentials` 改为 lazy import `evomaster.env.bohrium` |
| path_adaptor.py | 837 | oss_io.upload_file_to_oss + evomaster.env.{get_bohrium_storage_config, inject_bohrium_executor} | 搬入，改 `from .oss_io import upload_file_to_oss`；bohrium 函数保留 lazy import per D-08 |

**path_adaptor.py 的 bohrium 依赖处理（D-08）:**

当前代码（第 42 行）是顶级 import:
```python
from evomaster.env import get_bohrium_storage_config, inject_bohrium_executor
```

需要改为 lazy import（函数级），以保持 matmaster 可独立加载：
```python
# 在 _resolve_executor 和 resolve_args 中使用时才 import
def _resolve_executor(self, ...):
    from evomaster.env.bohrium import inject_bohrium_executor
    ...

def resolve_args(self, ...):
    from evomaster.env.bohrium import get_bohrium_storage_config
    ...
```

**job_service.py 的 bohrium credentials 依赖处理:**

当前 `_get_access_key` 函数（第 58-74 行）lazy import `evomaster.env.get_bohrium_credentials`。搬入后保持 lazy import，但路径改为 `evomaster.env.bohrium.get_bohrium_credentials`（直接 import 具体模块，避免触发 evomaster.env.__init__.py 的完整加载链）。

### Pattern 6: _build_tools 中 path_adaptor 注入变化

当前 _build_tools 为每个 MCPTool 设置 `mcp_tool._path_adaptor`。新架构中 LazyMCPTool 直接持有 path_adaptor，不再经过 MCPTool。

**cache_mcp_schemas.py 的 _build_tools 使用:**
cache_mcp_schemas 只需要 tool 的 name/description/input_schema，不需要实际执行。因此 _build_tools 生成的对象只需要暴露这三个属性。可以让精简版 manager 的 _build_tools 直接存储 dict 而非创建 MCPTool 对象。

### Anti-Patterns to Avoid
- **不要保留 MCPTool 包装层:** D-09 明确要求去掉中间层。即使在 cache_mcp_schemas 中也不应创建 MCPTool 实例
- **不要把 reconnection 逻辑搬入精简版 manager:** matmaster 的 LazyMCPConnector 是按需连接模式，不需要长连接 runner
- **不要把 bohrium 函数的顶级 import 带进 matmaster 模块:** 必须保持 lazy import，否则 matmaster 在没有 evomaster 的环境中无法加载

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| MCP 传输连接 | 自己实现 stdio/sse/http 连接 | MCPConnection（从 evomaster 搬入） | 已经封装好的 ABC + 工厂，与 MCP SDK 1.26 完美对接 |
| MCP call_tool | 自己处理 isError 检查和 content 解析 | MCPConnection.call_tool | 已处理 isError=True -> RuntimeError，content list 解析 |
| Tool schema filter/dedup | 自己写 filter 逻辑 | _build_tools 从 MCPToolManager 搬入 | 成熟逻辑，包含 include_only/sync_tools/dedup/description propagation |
| OSS 上传 | 自己对接 oss2 | upload_file_to_oss 从 oss_io.py 搬入 | 处理了 key 生成、basename 保留等细节 |
| Bohrium job ID 解析 | 自己实现 | _extract_bohr_job_id 从 job_service.py 搬入 | 处理了 numeric/hex/hash 三种格式 |

## Common Pitfalls

### Pitfall 1: path_adaptor 顶级 import bohrium 函数
**What goes wrong:** matmaster 在没有 evomaster 的环境中 import matmaster.adaptors.calculation 时立即失败
**Why it happens:** path_adaptor.py 第 42 行是顶级 import `from evomaster.env import get_bohrium_storage_config, inject_bohrium_executor`
**How to avoid:** 搬入时必须将第 42 行改为函数级 lazy import。verify: `python -c "from matmaster.adaptors.calculation import CalculationPathAdaptor"` 不应触发 evomaster import
**Warning signs:** ImportError at module load time

### Pitfall 2: MCPConnection.call_tool 返回值格式与 MCPTool._format_mcp_result 差异
**What goes wrong:** LazyMCPTool 直连 call_tool 后返回的是 MCP SDK 的 content list（TextContent/ImageContent 对象），而非 MCPTool 格式化后的字符串
**Why it happens:** 当前 MCPTool._format_mcp_result 处理了 content list -> str/dict 的转换。去掉中间层后需要 LazyMCPTool 自行处理
**How to avoid:** 将 _format_mcp_result 逻辑提取到 LazyMCPTool 中。MCPConnection.call_tool 返回的是 `result.content`（一个 list of content items），每个 item 有 `.text` 属性
**Warning signs:** tool 返回值格式变化导致 Agent 无法正确解析

### Pitfall 3: 精简版 add_server 缺少 timeout 和 retry
**What goes wrong:** MCP server 不可达时启动卡死
**Why it happens:** 原版 MCPToolManager 有 3 次重试 + MCP_CONNECT_TIMEOUT（15s）
**How to avoid:** 精简版 add_server 必须保留 `asyncio.wait_for(timeout=MCP_CONNECT_TIMEOUT)` 包装。3 次重试逻辑也建议保留（网络抖动时有用）
**Warning signs:** cache_mcp_schemas 在某个 server 不可达时永久挂起

### Pitfall 4: LazyMCPConnector 的 event loop 管理
**What goes wrong:** LazyMCPConnector 使用后台 event loop 线程，MCPConnection 的 async context manager 必须在同一个 loop 中进入和退出
**Why it happens:** MCPConnection.__aenter__ 创建的资源绑定到特定 event loop
**How to avoid:** 确保 add_server 和 cleanup 都通过 run_coroutine_threadsafe 提交到同一个 loop。当前 LazyMCPConnector 已正确处理了这点
**Warning signs:** RuntimeError: cannot schedule new futures after interpreter shutdown

### Pitfall 5: job_service.py 中 _get_access_key 的 evomaster.env 依赖
**What goes wrong:** _get_access_key fallback 到 evomaster.env.get_bohrium_credentials，搬入后如果不改会触发 evomaster import
**Why it happens:** 第 63 行 `from evomaster.env import get_bohrium_credentials`
**How to avoid:** 搬入后改为 `from evomaster.env.bohrium import get_bohrium_credentials`（直接 import 文件避免 __init__.py 拉入完整 evomaster.env），或者直接用环境变量 fallback（BOHRIUM_ACCESS_KEY 已在环境中）
**Warning signs:** ImportError 或意外拉入 evomaster.env 完整依赖链

### Pitfall 6: _build_tools 对 MCPTool 类的依赖
**What goes wrong:** 精简版 _build_tools 仍然 `from .mcp import MCPTool` 创建 evomaster MCPTool 实例
**Why it happens:** 原版 _build_tools 创建 MCPTool 实例并设置 _path_adaptor/_mcp_server 等属性
**How to avoid:** 精简版 _build_tools 应该存储轻量级数据对象（dict 或 dataclass），不创建 MCPTool。cache_mcp_schemas 只需要 name/description/input_schema/remote_tool_name
**Warning signs:** 无法去掉 evomaster.agent.tools.mcp.mcp 的 import

## Code Examples

### MCPConnection.call_tool 返回值处理

```python
# MCPConnection.call_tool 返回 result.content -- 一个 list
# 每个 item 是 TextContent(type='text', text='...') 等 MCP SDK 类型

async def execute(self, arguments: dict[str, Any]) -> ToolResult:
    result_content = await self._connection.call_tool(
        self._remote_tool_name, resolved_args
    )
    # result_content 是 list of content items
    # 需要提取 text 并格式化
    parts: list[str] = []
    for item in result_content:
        if hasattr(item, 'text'):
            parts.append(item.text)
        elif isinstance(item, dict) and 'text' in item:
            parts.append(item['text'])
        else:
            parts.append(str(item))

    if not parts:
        content = ''
    elif len(parts) == 1:
        # 单条结果：尝试解析为 JSON
        text = parts[0].strip()
        if text.startswith('{') or text.startswith('['):
            try:
                parsed = json.loads(text)
                content = json.dumps(parsed, ensure_ascii=False, default=str)
            except json.JSONDecodeError:
                content = text
        else:
            content = text
    else:
        content = '\n'.join(parts)

    return ToolResult(status="success", content=content)
```

### path_adaptor bohrium lazy import 转换

```python
# 搬入前 (path_adaptor.py 第 42 行):
from evomaster.env import get_bohrium_storage_config, inject_bohrium_executor

# 搬入后 (matmaster/adaptors/calculation/path_adaptor.py):
# 删除顶级 import，改为函数级 lazy import

def _resolve_executor(self, server_name, remote_tool_name, ...):
    from evomaster.env.bohrium import inject_bohrium_executor
    # ...原有逻辑...

def resolve_args(self, workspace_path, args, ...):
    # ...executor 注入后...
    from evomaster.env.bohrium import get_bohrium_storage_config
    out['storage'] = get_bohrium_storage_config(
        access_key=access_key, project_id=project_id, user_id=user_id,
    )
```

### monitor_job import 路径修改

```python
# 搬入前:
from evomaster.adaptors.calculation.job_service import (
    download_job_directory, download_job_file, get_file_token, iterate_job_files,
)

# 搬入后:
from matmaster.adaptors.calculation.job_service import (
    download_job_directory, download_job_file, get_file_token, iterate_job_files,
)
```

### 精简版 MCPToolManager._build_tools 数据存储

```python
# 不再创建 MCPTool 实例，存储 dict
class BuiltTool:
    """_build_tools 产出的轻量工具描述。"""
    __slots__ = ('name', 'remote_name', 'description', 'input_schema',
                 'server_name', 'connection')

    def __init__(self, name, remote_name, description, input_schema,
                 server_name, connection):
        self.name = name
        self.remote_name = remote_name
        self.description = description
        self.input_schema = input_schema
        self.server_name = server_name
        self.connection = connection
```

## Complete evomaster Import Inventory (Phase 27 Scope)

以下是 matmaster/ 中所有需要在本 phase 修改的 evomaster import 点:

| File | Line | Import | Action |
|------|------|--------|--------|
| `tools/lazy_mcp.py:20` | TYPE_CHECKING | `evomaster.agent.tools.mcp.mcp.MCPTool` | 删除（不再使用 MCPTool 类型） |
| `tools/lazy_mcp.py:21` | TYPE_CHECKING | `evomaster.agent.tools.mcp.mcp_manager.MCPToolManager` | 改为 `matmaster.mcp.manager.MCPToolManager` |
| `tools/lazy_mcp.py:112` | runtime | `evomaster.adaptors.calculation.get_calculation_path_adaptor` | 改为 `matmaster.adaptors.calculation.path_adaptor` |
| `tools/lazy_mcp.py:170` | runtime | `evomaster.agent.tools.mcp.mcp_manager.MCPToolManager` | 改为 `matmaster.mcp.manager.MCPToolManager` |
| `tools/cache_mcp_schemas.py:43` | runtime | `evomaster.agent.tools.mcp.mcp_manager.MCPToolManager` | 改为 `matmaster.mcp.manager.MCPToolManager` |
| `tools/cache_mcp_schemas.py:63` | runtime | `evomaster.adaptors.calculation.resolve_mcp_config_path` | 改为 `matmaster.adaptors.calculation.env_config` |
| `eval_tooling_snapshot.py:99` | runtime | `evomaster.adaptors.calculation.resolve_mcp_config_path` | 改为 `matmaster.adaptors.calculation.env_config` |
| `core/exp.py:466` | runtime | `evomaster.adaptors.calculation.resolve_mcp_config_path` | 改为 `matmaster.adaptors.calculation.env_config` |
| `tools/builtin/monitor_job/_download.py:27` | runtime | `evomaster.adaptors.calculation.job_service.*` | 改为 `matmaster.adaptors.calculation.job_service` |
| `tools/builtin/monitor_job/_lifecycle.py:81` | runtime | `evomaster.adaptors.calculation.job_service.*` | 改为 `matmaster.adaptors.calculation.job_service` |
| `tools/builtin/monitor_job/_logs.py:34` | runtime | `evomaster.adaptors.calculation.job_service.iterate_job_files` | 改为 `matmaster.adaptors.calculation.job_service` |
| `tools/builtin/monitor_job/_logs.py:106` | runtime | `evomaster.adaptors.calculation.job_service.*` | 改为 `matmaster.adaptors.calculation.job_service` |
| `tools/builtin/monitor_job/_llm.py:215` | runtime | `evomaster.adaptors.calculation.job_service.terminate_job` | 改为 `matmaster.adaptors.calculation.job_service` |

**注意:** `_llm.py:67-68` 的 `evomaster.config.ConfigManager` 和 `evomaster.utils.LLMConfig` 依赖 **不在本 phase 范围**。这些是 monitor_job LLM 决策子系统的依赖，属于更深层的 evomaster 耦合。

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| MCPTool 同步包装 + asyncio.to_thread | 直接 await MCPConnection.call_tool | Phase 27 (D-09) | 去掉中间层，减少线程切换开销 |
| evomaster MCPToolManager（672 行） | 精简版 matmaster MCPToolManager（~200 行） | Phase 27 (D-01, D-03) | 只保留 matmaster 使用的子集 |
| path_adaptor 顶级 import bohrium | 函数级 lazy import | Phase 27 (D-08) | matmaster 可在无 evomaster 环境加载 |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2+ with pytest-asyncio |
| Config file | `pytest.ini` |
| Quick run command | `uv run pytest tests/matmaster/tools/test_lazy_mcp.py -x` |
| Full suite command | `uv run pytest tests/matmaster/ -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MCP-01 | LazyMCPTool 独立执行（不依赖 evomaster MCPTool） | unit | `uv run pytest tests/matmaster/tools/test_lazy_mcp.py -x` | Exists, needs update |
| MCP-01 | MCPConnection 搬入后可正常 import | unit | `uv run pytest tests/matmaster/mcp/test_connection.py -x` | Wave 0 |
| MCP-01 | 精简版 MCPToolManager add_server + _build_tools | unit | `uv run pytest tests/matmaster/mcp/test_manager.py -x` | Wave 0 |
| MCP-01 | cache_mcp_schemas 使用 matmaster 原生 manager | unit | `uv run pytest tests/matmaster/tools/test_cache_mcp_schemas.py -x` | Wave 0 |
| CALC-01 | env_config 搬入 matmaster 后可正常调用 | unit | `uv run pytest tests/matmaster/adaptors/test_env_config.py -x` | Wave 0 |
| CALC-01 | path_adaptor 搬入后 resolve_args 正常工作 | unit | `uv run pytest tests/matmaster/adaptors/test_path_adaptor.py -x` | Wave 0 |
| CALC-01 | job_service 搬入后 query_job_status 正常 | unit | `uv run pytest tests/matmaster/adaptors/test_job_service.py -x` | Wave 0 |
| CALC-02 | path_adaptor resolve_args 输出与搬入前完全一致 | unit | `uv run pytest tests/matmaster/adaptors/test_path_adaptor.py::test_resolve_args_compat -x` | Wave 0 |
| ALL | matmaster/ 不再 import evomaster MCP/calculation 路径 | audit | `uv run python -c "import ast, sys; [check matmaster imports]"` | Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/tools/test_lazy_mcp.py tests/matmaster/mcp/ tests/matmaster/adaptors/ -x`
- **Per wave merge:** `uv run pytest tests/matmaster/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/matmaster/mcp/__init__.py` + `test_connection.py` -- MCPConnection import 验证
- [ ] `tests/matmaster/mcp/test_manager.py` -- 精简版 MCPToolManager 单元测试
- [ ] `tests/matmaster/adaptors/__init__.py` + `calculation/__init__.py`
- [ ] `tests/matmaster/adaptors/calculation/test_env_config.py` -- resolve_mcp_config_path
- [ ] `tests/matmaster/adaptors/calculation/test_path_adaptor.py` -- resolve_args 兼容性
- [ ] `tests/matmaster/adaptors/calculation/test_job_service.py` -- job ID 解析
- [ ] `tests/matmaster/tools/test_lazy_mcp.py` -- 需要更新以适配新的直连架构（不再使用 FakeConnector.connect_and_get_tool 模式）
- [ ] Import audit script -- 验证 matmaster/ 不再 import evomaster.agent.tools.mcp 或 evomaster.adaptors.calculation

## Open Questions

1. **_build_tools 中 MCPTool._path_adaptor 的注入方式**
   - What we know: 当前 _build_tools 为每个 MCPTool 实例设置 `mcp_tool._path_adaptor = self.path_adaptor_factory()`
   - What's unclear: 精简版中不再创建 MCPTool，path_adaptor 何时/如何传递给 LazyMCPTool
   - Recommendation: 精简版 _build_tools 只存储 tool 元数据。path_adaptor 由 LazyMCPConnector 在创建 LazyMCPTool 时注入（已有 configure_mcp_manager 中的 factory 逻辑）

2. **cache_mcp_schemas.py 对 tool.get_tool_spec() 的依赖**
   - What we know: 当前使用 MCPTool.get_tool_spec() 获取 ToolSpec 对象，从中提取 name/description/parameters
   - What's unclear: 精简版 manager 的 _build_tools 不创建 MCPTool 后如何获取这些字段
   - Recommendation: _build_tools 已有 tools_info（原始 list_tools 返回），直接用 tool_info['name']/['description']/['input_schema']。唯一需要注意的是 remote_tool_name（original_name vs prefixed_name）

3. **monitor_job/_llm.py:67-68 的 evomaster.config/utils 依赖**
   - What we know: 这些依赖属于 LLM 调用子系统，与 MCP/calculation 无关
   - What's unclear: 是否在本 phase 处理
   - Recommendation: 不在本 phase 范围。CONTEXT.md 明确列出的迁移文件不包含这两个 import。后续 phase 处理

## Sources

### Primary (HIGH confidence)
- Source code: `evomaster/agent/tools/mcp/mcp_connection.py` (222 行) -- MCPConnection 完整实现
- Source code: `evomaster/agent/tools/mcp/mcp_manager.py` (672 行) -- MCPToolManager 完整实现
- Source code: `evomaster/agent/tools/mcp/mcp.py` (464 行) -- MCPTool path_adaptor resolve 逻辑
- Source code: `evomaster/adaptors/calculation/path_adaptor.py` (837 行) -- CalculationPathAdaptor
- Source code: `evomaster/adaptors/calculation/env_config.py` (75 行) -- resolve_mcp_config_path
- Source code: `evomaster/adaptors/calculation/oss_io.py` (140 行) -- OSS I/O
- Source code: `evomaster/adaptors/calculation/job_service.py` (682 行) -- Bohrium job service
- Source code: `evomaster/env/bohrium.py` (186 行) -- get_bohrium_storage_config, inject_bohrium_executor
- Source code: `matmaster/tools/lazy_mcp.py` (210 行) -- 当前 LazyMCPTool + LazyMCPConnector
- Source code: `matmaster/tools/cache_mcp_schemas.py` (116 行) -- schema cache CLI
- Source code: `matmaster/core/exp.py` (line 464-468) -- resolve_mcp_config_path usage
- Source code: `matmaster/eval_tooling_snapshot.py` (line 97-101) -- resolve_mcp_config_path usage
- Source code: `matmaster/tools/builtin/monitor_job/_*.py` -- job_service lazy imports
- Package version: `mcp` 1.26.0, `oss2` 2.19.1, `anyio` 4.12.1, `httpx` 0.28.1 -- pip show 验证

### Secondary (MEDIUM confidence)
- Existing tests: `tests/matmaster/tools/test_lazy_mcp.py` -- 现有测试覆盖 LazyMCPTool Protocol + configure_mcp_manager

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- 不引入新依赖，全部从现有代码搬入
- Architecture: HIGH -- 迁移方案由 CONTEXT.md 锁定决策明确约束
- Pitfalls: HIGH -- 基于源码逐行分析得出，所有依赖链已完整追溯

**Research date:** 2026-04-01
**Valid until:** 2026-05-01 (stable codebase, migration phase)
