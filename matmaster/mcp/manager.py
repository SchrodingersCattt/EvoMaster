"""精简版 MCP 工具管理器

只保留 matmaster 实际使用的功能：add_server、_build_tools、cleanup。
不含 runner task、重连、进度回调、ToolRegistry 注册等 evomaster 冗余逻辑。
tools_by_server 存储轻量级 dict 而非 MCPTool 实例。

Connection lifecycle:
  每个 MCP 连接由一个 long-lived asyncio Task (_ManagedConn) 持有。
  __aenter__ 和 __aexit__ 始终在同一个 Task 中执行，避免 anyio
  cancel scope 跨 Task 报 RuntimeError 的问题。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from .connection import MCP_CONNECT_TIMEOUT, MCPConnection, create_connection

logger = logging.getLogger(__name__)

# 最大重试次数
_MAX_RETRIES = 3

# Per-connection shutdown budget (seconds).
# One hung connection must not prevent later connections from being cleaned up.
_PER_CONN_SHUTDOWN_TIMEOUT = 1.0

# 重试间隔（秒）
_RETRY_DELAY = 2


class _ManagedConn:
    """Hold an MCPConnection context in a single long-lived Task.

    anyio cancel scopes must be entered and exited in the same asyncio Task.
    run_coroutine_threadsafe creates a NEW Task per call, so calling
    __aenter__ in one call and __aexit__ in another triggers RuntimeError.

    _ManagedConn solves this by running ``async with conn_ctx`` in one
    persistent Task.  ``add_server`` awaits the ready future to get the
    connection; ``cleanup`` sets the shutdown event which causes the
    ``async with`` to exit -- both in the same Task.
    """

    def __init__(self, conn_ctx: MCPConnection) -> None:
        self._conn_ctx = conn_ctx
        self._shutdown = asyncio.Event()
        self._ready: asyncio.Future[MCPConnection] = (
            asyncio.get_running_loop().create_future()
        )
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            async with self._conn_ctx as conn:
                self._ready.set_result(conn)
                await self._shutdown.wait()
        except BaseException as exc:
            if not self._ready.done():
                self._ready.set_exception(exc)

    async def wait_ready(self, timeout: float) -> MCPConnection:
        return await asyncio.wait_for(self._ready, timeout=timeout)

    async def close(self, timeout: float) -> None:
        self._shutdown.set()
        try:
            await asyncio.wait_for(self._task, timeout=timeout)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        except Exception:
            pass


class MCPToolManager:
    """精简版 MCP 工具管理器

    管理 MCP 服务器连接和工具信息。
    tools_by_server 存储轻量级 dict（而非 MCPTool 实例），
    用于 LazyMCPTool 和 cache_mcp_schemas 等下游消费者。

    核心方法：
    - add_server: 连接 MCP server 并获取工具信息
    - _build_tools: 从 list_tools 结果构建工具信息字典
    - cleanup: 关闭所有连接
    """

    def __init__(self) -> None:
        # MCP 连接：{server_name: MCPConnection}
        self.connections: dict[str, MCPConnection] = {}

        # 按服务器组织的工具信息：{server_name: {tool_name: tool_info_dict}}
        # tool_info_dict 结构：
        #   {"name": str, "description": str, "input_schema": dict,
        #    "remote_tool_name": str, "connection": MCPConnection,
        #    "has_path_adaptor": bool}
        self.tools_by_server: dict[str, dict[str, dict[str, Any]]] = {}

        # Long-lived managed connections (enter/exit in same Task)
        self._managed: dict[str, _ManagedConn] = {}

        # 需要 path adaptor 的 server 集合
        self.path_adaptor_servers: set[str] = set()

        # path adaptor 工厂函数
        self.path_adaptor_factory: Callable[[], Any] | None = None

        # 同步工具集合：{server_name: {tool_name, ...}}
        # 对于 calculation 服务，submit_<name> 在 <name> 属于 sync_tools 时被过滤掉
        self.sync_tools_by_server: dict[str, set[str]] = {}

        # 工具白名单：{server_name: [tool_name, ...]}
        # 若设置，只保留白名单中的工具
        self.tool_include_only: dict[str, list[str]] = {}

        # 事件循环引用（用于 run_coroutine_threadsafe 等跨线程场景）
        self.loop: asyncio.AbstractEventLoop | None = None

        # 全局去重追踪（同名 tool 只保留第一个 server 的）
        self._seen_tools: set[str] = set()

    async def add_server(
        self, name: str, transport: str, **connection_kwargs: Any
    ) -> None:
        """连接 MCP server、获取 tools、构建 tool 信息。

        使用 create_connection 工厂创建连接，带超时和重试。
        成功后调用 _build_tools 构建工具信息字典。

        Args:
            name: 服务器名称
            transport: 传输方式（stdio/sse/http）
            **connection_kwargs: 传递给 create_connection 的参数
        """
        if name in self.connections:
            raise ValueError(f"MCP server '{name}' already exists")

        logger.info("Adding MCP server: %s (%s)", name, transport)

        # 确定可重试的异常类型
        try:
            import httpx

            _retry_exc = (
                httpx.ReadError,
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.ConnectTimeout,
                asyncio.TimeoutError,
            )
        except ImportError:
            _retry_exc = (OSError, asyncio.TimeoutError)

        last_error: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            managed: _ManagedConn | None = None
            try:
                conn_ctx = create_connection(transport=transport, **connection_kwargs)
                managed = _ManagedConn(conn_ctx)

                # 带超时的连接（__aenter__ 在 managed task 内执行）
                conn = await managed.wait_ready(timeout=MCP_CONNECT_TIMEOUT)

                # 带超时的 list_tools
                try:
                    tools_info = await asyncio.wait_for(
                        conn.list_tools(),
                        timeout=MCP_CONNECT_TIMEOUT,
                    )
                except Exception:
                    await managed.close(timeout=_PER_CONN_SHUTDOWN_TIMEOUT)
                    raise

                # 连接成功
                self.connections[name] = conn
                self._managed[name] = managed

                logger.info(
                    "Found %d tools from MCP server '%s'", len(tools_info), name
                )
                self._build_tools(name, conn, tools_info)
                logger.info("Successfully added MCP server '%s'", name)
                return

            except _retry_exc as e:
                last_error = e
                if attempt < _MAX_RETRIES:
                    logger.warning(
                        "MCP server '%s' connection failed (attempt %d/%d), "
                        "retrying in %ds: %s",
                        name,
                        attempt,
                        _MAX_RETRIES,
                        _RETRY_DELAY,
                        e,
                    )
                    await asyncio.sleep(_RETRY_DELAY)
                else:
                    logger.error(
                        "MCP server '%s' failed after %d attempts: %s",
                        name,
                        _MAX_RETRIES,
                        e,
                    )

        # 所有重试失败
        raise RuntimeError(
            f"Failed to connect MCP server '{name}' after {_MAX_RETRIES} attempts"
        ) from last_error

    def _build_tools(
        self,
        name: str,
        conn: MCPConnection,
        tools_info: list[dict[str, Any]],
    ) -> None:
        """从 list_tools 结果构建工具信息字典。

        应用以下过滤和变换：
        1. tool_include_only 白名单过滤
        2. sync_tools 过滤（移除 submit_* 对应的同步工具）
        3. async 去重（当 submit_X 存在时移除 base X）
        4. description 继承（submit_* 继承 base 的完整描述）
        5. path_adaptor 注入标记
        6. 全局去重（同名 tool 只保留第一个 server）

        结果存储为轻量级 dict 而非 MCPTool 实例。

        Args:
            name: 服务器名称
            conn: MCP 连接
            tools_info: list_tools 返回的工具信息列表
        """
        # 1. tool_include_only 白名单过滤
        include_only = self.tool_include_only.get(name)
        if include_only is not None:
            tools_info = [t for t in tools_info if t.get('name') in include_only]
            logger.info(
                "Filtered to %d tools for server '%s' (include_only: %s)",
                len(tools_info),
                name,
                include_only,
            )

        # 2. sync_tools 过滤
        sync_tools = self.sync_tools_by_server.get(name, set())
        if sync_tools:
            before = len(tools_info)
            tools_info = [
                t
                for t in tools_info
                if not (
                    t.get('name', '').startswith('submit_')
                    and t.get('name', '')[len('submit_') :] in sync_tools
                )
            ]
            if len(tools_info) < before:
                logger.info(
                    "Filtered out submit_* for sync_tools on server '%s' "
                    "(sync_tools=%s); %d -> %d tools",
                    name,
                    sync_tools,
                    before,
                    len(tools_info),
                )

        # 构建 base 描述映射（submit_* 描述继承用）
        base_descriptions: dict[str, str] = {}
        for tool_info in tools_info:
            tool_name = tool_info.get('name', '')
            desc = tool_info.get('description', '')
            if not tool_name.startswith('submit_') and desc:
                base_descriptions[tool_name] = desc

        # 3. async 去重：当 submit_X 存在时移除 base X
        submit_names = {
            t.get('name', '')[len('submit_') :]
            for t in tools_info
            if t.get('name', '').startswith('submit_')
        }
        if submit_names:
            before_dedup = len(tools_info)
            tools_info = [
                t
                for t in tools_info
                if t.get('name', '').startswith('submit_')
                or t.get('name', '') not in submit_names
            ]
            if len(tools_info) < before_dedup:
                logger.info(
                    "Dedup: removed %d base tools with submit_* counterparts "
                    "on server '%s'",
                    before_dedup - len(tools_info),
                    name,
                )

        # 判断是否需要 path_adaptor
        needs_path_adaptor = (
            self.path_adaptor_servers
            and self.path_adaptor_factory
            and name in self.path_adaptor_servers
        )

        # 构建工具信息字典
        server_tools: dict[str, dict[str, Any]] = {}
        for tool_info in tools_info:
            original_name = tool_info['name']
            prefixed_name = f"{name}_{original_name}"

            # 全局去重
            if prefixed_name in self._seen_tools:
                logger.debug(
                    "Skipping duplicate tool '%s' from server '%s'",
                    prefixed_name,
                    name,
                )
                continue
            self._seen_tools.add(prefixed_name)

            # 4. description 继承
            description = tool_info.get('description', '')
            if original_name.startswith('submit_'):
                base_name = original_name[len('submit_') :]
                base_desc = base_descriptions.get(base_name, '')
                if base_desc and len(base_desc) > len(description):
                    description = base_desc

            tool_dict: dict[str, Any] = {
                'name': prefixed_name,
                'description': description,
                'input_schema': tool_info.get('input_schema', {}),
                'remote_tool_name': original_name,
                'connection': conn,
                'has_path_adaptor': bool(needs_path_adaptor),
            }

            server_tools[prefixed_name] = tool_dict

        self.tools_by_server[name] = server_tools

    async def cleanup(self) -> None:
        """关闭所有 MCP 连接。

        通过 _ManagedConn.close() 触发 shutdown event，让持有连接的
        long-lived task 正常退出 async with（__aexit__ 在同一个 Task 中）。
        每个连接有独立的 shutdown budget。
        """
        logger.info("Cleaning up MCP connections")

        for name, managed in list(self._managed.items()):
            try:
                await managed.close(timeout=_PER_CONN_SHUTDOWN_TIMEOUT)
                logger.debug("Closed MCP connection: %s", name)
            except Exception as e:
                logger.warning("Error closing MCP connection '%s': %s", name, e)

        self.connections.clear()
        self.tools_by_server.clear()
        self._managed.clear()
        self._seen_tools.clear()

        logger.info("MCP cleanup complete")
