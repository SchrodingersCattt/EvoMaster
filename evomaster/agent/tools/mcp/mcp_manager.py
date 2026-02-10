"""MCP 工具管理器

负责 MCP 连接的初始化、工具注册和生命周期管理。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..base import ToolRegistry
    from .mcp import MCPTool


class MCPToolManager:
    """MCP 工具管理器

    管理 MCP 服务器连接和工具注册。
    采用混合方案（方案C）：
    - 对外：注册到统一的 ToolRegistry
    - 对内：独立管理 MCP 连接和工具

    职责：
    1. 管理 MCP 服务器连接
    2. 创建 MCPTool 实例
    3. 按服务器组织工具
    4. 注册到 ToolRegistry
    5. 生命周期管理（添加/移除服务器）

    使用示例：
        manager = MCPToolManager()

        # 添加 MCP 服务器
        await manager.add_server(
            name="github",
            transport="stdio",
            command="python",
            args=["mcp_servers/github_server.py"]
        )

        # 注册到 ToolRegistry
        manager.register_tools(tool_registry)

        # 清理
        await manager.cleanup()
    """

    def __init__(self):
        """初始化 MCP 工具管理器"""
        # MCP 连接：{server_name: MCPConnection}
        self.connections: dict[str, Any] = {}

        # 按服务器组织的工具：{server_name: {tool_name: MCPTool}}
        self.tools_by_server: dict[str, dict[str, MCPTool]] = {}

        # 已注册到的 ToolRegistry（用于后续移除工具）
        self._registered_registry: ToolRegistry | None = None

        self.logger = logging.getLogger(self.__class__.__name__)
        self.loop: asyncio.AbstractEventLoop | None = None

        # server runner task：{name: asyncio.Task}
        self._server_tasks: dict[str, asyncio.Task] = {}

        # stop signal：{name: asyncio.Event}
        self._server_stop: dict[str, asyncio.Event] = {}

        # ready signal：{name: asyncio.Event}
        self._server_ready: dict[str, asyncio.Event] = {}

        # Optional path adaptor for Mat-style MCP (local path -> OSS URL)
        self.path_adaptor_servers: set[str] = set()
        self.path_adaptor_factory: Any = None

        # Optional per-server tool filter: only register these tools (original names). If set for a server, tools not in the list are excluded.
        # Example: {"mat_sn": ["web-search", "search-papers-enhanced"]} -> only those two from mat_sn.
        self.tool_include_only: dict[str, list[str]] = {}

        # Reconnection support: runner 监听此事件以触发重连
        self._reconnect_events: dict[str, asyncio.Event] = {}
        # 等待重连完成的线程级 Event 列表（由 request_reconnect 添加，runner 完成后 set）
        self._reconnect_waiters: dict[str, list[threading.Event]] = {}

    def _build_tools(self, server_name: str, connection: Any, tools_info: list[dict]) -> None:
        from .mcp import MCPTool

        include_only = self.tool_include_only.get(server_name)
        if include_only is not None:
            tools_info = [t for t in tools_info if t.get("name") in include_only]
            self.logger.info(f"Filtered to {len(tools_info)} tools for server '{server_name}' (include_only: {include_only})")

        server_tools: dict[str, MCPTool] = {}
        for tool_info in tools_info:
            original_name = tool_info["name"]
            prefixed_name = f"{server_name}_{original_name}"

            mcp_tool = MCPTool(
                mcp_connection=connection,
                tool_name=prefixed_name,
                tool_description=tool_info.get("description", ""),
                input_schema=tool_info.get("input_schema", {}),
                remote_tool_name=original_name,
            )
            mcp_tool._mcp_server = server_name
            mcp_tool._mcp_loop = self.loop  # 你原来注入 loop 的逻辑保留
            mcp_tool._mcp_manager = self  # 用于断线重连
            if self.path_adaptor_servers and self.path_adaptor_factory and server_name in self.path_adaptor_servers:
                mcp_tool._path_adaptor = self.path_adaptor_factory()

            server_tools[prefixed_name] = mcp_tool

        self.tools_by_server[server_name] = server_tools

    def _update_tool_connections(self, server_name: str, new_connection: Any) -> None:
        """重连后，原地更新已有 MCPTool 的 connection 引用（避免重新创建对象）。"""
        tools = self.tools_by_server.get(server_name, {})
        for tool in tools.values():
            tool.mcp_connection = new_connection
        if tools:
            self.logger.debug(
                "Updated %d tool connection(s) for server '%s'", len(tools), server_name
            )

    def _notify_reconnect_waiters(self, server_name: str) -> None:
        """通知所有等待重连完成的线程。"""
        waiters = self._reconnect_waiters.pop(server_name, [])
        for w in waiters:
            w.set()

    def request_reconnect(self, server_name: str) -> threading.Event:
        """请求某个 MCP server 重连（线程安全）。

        返回一个 threading.Event，重连完成（或不可重连）时会被 set。
        调用方可以 done.wait(timeout=...) 阻塞等待。
        """
        done_event = threading.Event()

        reconnect_evt = self._reconnect_events.get(server_name)
        if not reconnect_evt or not self.loop or self.loop.is_closed():
            done_event.set()  # 无法重连，立即返回
            return done_event

        def _trigger():
            # 在 MCP loop 线程中执行，避免竞态
            if server_name not in self._reconnect_waiters:
                self._reconnect_waiters[server_name] = []
            self._reconnect_waiters[server_name].append(done_event)
            reconnect_evt.set()

        self.loop.call_soon_threadsafe(_trigger)
        return done_event

    async def add_server(self, name: str, transport: str, **connection_kwargs) -> None:
        if name in self._server_tasks:
            raise ValueError(f"MCP server '{name}' already exists")

        if self.loop is None:
            # 方案A必须要求 manager.loop 已经被设置到一个长期运行的 loop
            raise RuntimeError("MCPToolManager.loop is None. Set a long-running event loop before add_server().")

        self.logger.info(f"Adding MCP server: {name} ({transport})")

        stop_evt = asyncio.Event()
        ready_evt = asyncio.Event()
        self._server_stop[name] = stop_evt
        self._server_ready[name] = ready_evt

        async def runner():
            from .mcp_connection import create_connection

            try:
                import httpx
                _retry_exc = (httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout)
            except ImportError:
                _retry_exc = (OSError, asyncio.TimeoutError)

            reconnect_evt = asyncio.Event()
            self._reconnect_events[name] = reconnect_evt
            first_connect = True

            while not stop_evt.is_set():
                reconnect_evt.clear()

                for attempt in range(1, 4):
                    try:
                        async with create_connection(transport=transport, **connection_kwargs) as conn:
                            self.connections[name] = conn

                            tools_info = await conn.list_tools()

                            if first_connect:
                                self.logger.info(f"Found {len(tools_info)} tools from MCP server '{name}'")
                                self._build_tools(name, conn, tools_info)
                                if self._registered_registry:
                                    for tool in self.tools_by_server[name].values():
                                        self._registered_registry.register(tool)
                            else:
                                self.logger.info(f"Reconnected MCP server '{name}', {len(tools_info)} tools")
                                self._update_tool_connections(name, conn)

                            ready_evt.set()
                            first_connect = False

                            # 通知所有等待重连的线程
                            self._notify_reconnect_waiters(name)

                            # 等待 stop 或 reconnect 信号
                            stop_task = asyncio.create_task(stop_evt.wait())
                            recon_task = asyncio.create_task(reconnect_evt.wait())
                            _done, pending = await asyncio.wait(
                                [stop_task, recon_task],
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            for t in pending:
                                t.cancel()
                                try:
                                    await t
                                except asyncio.CancelledError:
                                    pass

                            if stop_evt.is_set():
                                return  # 正常关闭
                            # else: 收到重连信号，退出 async with 以释放旧连接
                        break  # 成功退出 context，跳出重试循环
                    except _retry_exc as e:
                        if attempt < 3:
                            self.logger.warning(
                                "MCP server '%s' connection failed (attempt %s/3), retrying in 2s: %s",
                                name, attempt, e,
                            )
                            await asyncio.sleep(2)
                        else:
                            self.logger.error("MCP server '%s' failed after 3 attempts: %s", name, e)
                            # 通知等待者失败
                            self._notify_reconnect_waiters(name)
                            if first_connect:
                                ready_evt.set()
                                raise
                            # 非首次连接：等待后重试整个 while 循环
                            self.logger.info(f"Will retry reconnection for '{name}' in 5s")
                            await asyncio.sleep(5)
                    except BaseException:
                        self._notify_reconnect_waiters(name)
                        ready_evt.set()
                        raise

        # ✅ 必须保证 runner task 在 self.loop 里创建
        if asyncio.get_running_loop() is not self.loop:
            raise RuntimeError(
                "add_server() must be called inside MCP loop. "
                "Use run_coroutine_threadsafe(...) to submit it to manager.loop."
            )

        task = asyncio.create_task(runner())


        self._server_tasks[name] = task

        # 等待 tools 加载完
        await ready_evt.wait()
        if task.done() and (exc := task.exception()) is not None:
            # 清理掉登记
            self._server_tasks.pop(name, None)
            self._server_stop.pop(name, None)
            self._server_ready.pop(name, None)
            self.connections.pop(name, None)
            self.tools_by_server.pop(name, None)
            raise exc
        self.logger.info(f"Successfully added MCP server '{name}'")


    def register_tools(self, tool_registry: ToolRegistry) -> None:
        """将所有 MCP 工具注册到 ToolRegistry

        Args:
            tool_registry: 目标工具注册表
        """
        self._registered_registry = tool_registry

        total_count = 0
        for server_name, tools in self.tools_by_server.items():
            for tool_name, tool in tools.items():
                tool_registry.register(tool)
                total_count += 1
                self.logger.debug(f"Registered MCP tool: {tool_name} (from {server_name})")

        self.logger.info(f"Registered {total_count} MCP tools to ToolRegistry")

    async def remove_server(self, server_name: str) -> None:
        if asyncio.get_running_loop() is not self.loop:
            raise RuntimeError("remove_server() must be called inside MCP loop.")
        if server_name not in self._server_tasks:
            raise ValueError(f"MCP server '{server_name}' not found")

        self.logger.info(f"Removing MCP server: {server_name}")

        # 1) 从 ToolRegistry 中移除工具
        if self._registered_registry and server_name in self.tools_by_server:
            for tool_name in list(self.tools_by_server[server_name].keys()):
                self._registered_registry.unregister(tool_name)

        # 2) 让 runner 自己退出 async with（✅ __aexit__ 会在同一个 task 内执行）
        stop_evt = self._server_stop.get(server_name)
        if stop_evt:
            stop_evt.set()

        task = self._server_tasks.get(server_name)
        if task:
            await task  # 等它 clean exit

        # 3) 清理本地记录
        self._server_tasks.pop(server_name, None)
        self._server_stop.pop(server_name, None)
        self._server_ready.pop(server_name, None)

        self.connections.pop(server_name, None)
        tool_count = len(self.tools_by_server.get(server_name, {}))
        self.tools_by_server.pop(server_name, None)

        # 清理重连状态并释放等待者
        self._reconnect_events.pop(server_name, None)
        self._notify_reconnect_waiters(server_name)

        self.logger.info(f"Removed {tool_count} tools from server '{server_name}'")

    async def reload_server(self, server_name: str) -> None:
        """重新加载 MCP 服务器的工具

        用于工具热重载场景。

        Args:
            server_name: 服务器名称

        Raises:
            ValueError: 服务器不存在
        """
        if server_name not in self.connections:
            raise ValueError(f"MCP server '{server_name}' not found")

        self.logger.info(f"Reloading MCP server: {server_name}")

        # 保存连接配置（简化实现，实际可能需要保存完整配置）
        connection = self.connections[server_name]

        # 移除并重新添加
        # 注意：这里简化处理，实际应该保存原始配置
        await self.remove_server(server_name)

        # 重新获取工具
        tools_info = await connection.list_tools()

        # 重新创建工具（类似 add_server 的逻辑）
        from .mcp import MCPTool

        server_tools = {}
        for tool_info in tools_info:
            original_name = tool_info["name"]
            prefixed_name = f"{server_name}_{original_name}"

            mcp_tool = MCPTool(
                mcp_connection=connection,
                tool_name=prefixed_name,
                tool_description=tool_info.get("description", ""),
                input_schema=tool_info.get("input_schema", {}),
            )
            mcp_tool._mcp_server = server_name

            server_tools[prefixed_name] = mcp_tool

        self.tools_by_server[server_name] = server_tools
        self.connections[server_name] = connection

        # 重新注册
        if self._registered_registry:
            for tool in server_tools.values():
                self._registered_registry.register(tool)

        self.logger.info(f"Reloaded {len(server_tools)} tools from server '{server_name}'")

    async def cleanup(self) -> None:
        """清理所有 MCP 连接"""
        self.logger.info("Cleaning up MCP connections")
        
        if asyncio.get_running_loop() is not self.loop:
            raise RuntimeError("cleanup() must be called inside MCP loop.")

        failed: list[str] = []

        for name in list(self._server_tasks.keys()):
            try:
                await self.remove_server(name)
            except Exception as e:
                failed.append(name)
                self.logger.warning(f"Error cleaning up MCP server '{name}': {e}")

        if failed:
            self.logger.warning(f"MCP cleanup incomplete; failed servers: {failed}")
            return

        self.connections.clear()
        self.tools_by_server.clear()
        self._registered_registry = None

        self.logger.info("MCP cleanup complete")

    def get_tool_names(self) -> list[str]:
        """获取所有 MCP 工具名称

        Returns:
            工具名称列表
        """
        names = []
        for tools in self.tools_by_server.values():
            names.extend(tools.keys())
        return names

    def get_server_names(self) -> list[str]:
        """获取所有 MCP 服务器名称

        Returns:
            服务器名称列表
        """
        return list(self.connections.keys())

    def get_tools_by_server(self, server_name: str) -> list[MCPTool]:
        """获取特定服务器的所有工具

        Args:
            server_name: 服务器名称

        Returns:
            工具列表
        """
        return list(self.tools_by_server.get(server_name, {}).values())

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息

        Returns:
            统计信息字典
        """
        stats = {
            "total_servers": len(self.connections),
            "total_tools": len(self.get_tool_names()),
            "servers": {}
        }

        for server_name, tools in self.tools_by_server.items():
            server_stats = {
                "tool_count": len(tools),
                "tools": {}
            }
            for tool_name, tool in tools.items():
                server_stats["tools"][tool_name] = tool.get_stats()
            stats["servers"][server_name] = server_stats

        return stats
