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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .connection import MCP_CONNECT_TIMEOUT, MCPConnection, create_connection

logger = logging.getLogger(__name__)

# 最大重试次数
_MAX_RETRIES = 3

# Per-connection shutdown budget (seconds).
# One hung connection must not prevent later connections from being cleaned up.
_PER_CONN_SHUTDOWN_TIMEOUT = 1.0

# 重试间隔（秒）
_RETRY_DELAY = 2


class ManagedConnClosing(RuntimeError):
    pass


class ManagedConnBackpressure(RuntimeError):
    pass


class ManagedConnDead(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MCPConcurrencyPolicy:
    mode: str
    max_inflight: int
    max_pending_requests: int

    @classmethod
    def default_for_transport(cls, transport: str) -> MCPConcurrencyPolicy:
        transport = transport.lower()
        if transport == "stdio":
            return cls(mode="serial", max_inflight=1, max_pending_requests=16)
        return cls(mode="multiplex", max_inflight=4, max_pending_requests=64)


@dataclass
class _StartupState:
    connection: MCPConnection
    tools_info: list[dict[str, Any]]


@dataclass
class _CallToolRequest:
    tool_name: str
    arguments: dict[str, Any]
    result: asyncio.Future[Any]


_CLOSE_REQUEST = object()


class _ManagedConn:
    """Hold an MCPConnection context in a single long-lived Task.

    anyio cancel scopes must be entered and exited in the same asyncio Task.
    run_coroutine_threadsafe creates a NEW Task per call, so calling
    __aenter__ in one call and __aexit__ in another triggers RuntimeError.

    _ManagedConn solves this by running ``async with conn_ctx`` in one
    persistent Task. Startup (`list_tools`), request handling (`call_tool`),
    and shutdown all stay inside that same Task.
    """

    def __init__(
        self,
        conn_ctx: MCPConnection,
        *,
        max_inflight: int = 1,
        max_pending_requests: int = 16,
    ) -> None:
        self._conn_ctx = conn_ctx
        self._max_inflight = max(1, max_inflight)
        self._ready: asyncio.Future[_StartupState] = (
            asyncio.get_running_loop().create_future()
        )
        queue_size = max(1, max_pending_requests)
        self._requests: asyncio.Queue[_CallToolRequest | object] = asyncio.Queue(
            maxsize=queue_size
        )
        self._sem = asyncio.Semaphore(self._max_inflight)
        self._active_tasks: set[asyncio.Task[None]] = set()
        self._owner_wakeup = asyncio.Event()
        self._close_requested = asyncio.Event()
        self._drain_event = asyncio.Event()
        self._drain_event.set()
        self._closing = False
        self._fatal_error: BaseException | None = None
        self._closed = False
        self._task = asyncio.create_task(self._run())

    def _set_request_exception(
        self, request: _CallToolRequest, exc: BaseException
    ) -> None:
        if not request.result.done():
            request.result.set_exception(exc)

    def _wake_owner(self) -> None:
        self._owner_wakeup.set()

    def _make_dead_error(self, exc: BaseException) -> ManagedConnDead:
        dead = ManagedConnDead("MCP connection is unavailable")
        dead.__cause__ = exc
        return dead

    def _make_closing_error(self) -> ManagedConnClosing:
        return ManagedConnClosing("MCP connection is closing")

    def _map_cancelled_request_exception(
        self, exc: asyncio.CancelledError
    ) -> BaseException:
        if self._closing or self._close_requested.is_set():
            return self._make_closing_error()

        cause = self._fatal_error if self._fatal_error is not None else exc
        return self._make_dead_error(cause)

    def _failure_for_owner_exit(self, exc: BaseException) -> BaseException:
        if self._closing or self._close_requested.is_set():
            return self._make_closing_error()
        return self._make_dead_error(exc)

    def _fail_queued_requests(self, exc: BaseException) -> None:
        while True:
            try:
                request = self._requests.get_nowait()
            except asyncio.QueueEmpty:
                return

            if request is _CLOSE_REQUEST:
                continue
            self._set_request_exception(request, exc)

    def _track_active_task(self, task: asyncio.Task[None]) -> None:
        self._active_tasks.add(task)
        self._drain_event.clear()
        task.add_done_callback(self._on_request_done)

    def _cancel_active_task_for_result(
        self,
        request: _CallToolRequest,
        task: asyncio.Task[None],
    ) -> None:
        def _cancel_on_result_done(result: asyncio.Future[Any]) -> None:
            if not result.cancelled() or task.done():
                return
            task.cancel()
            self._wake_owner()

        request.result.add_done_callback(_cancel_on_result_done)

    def _on_request_done(self, task: asyncio.Task[None]) -> None:
        self._active_tasks.discard(task)
        if not self._active_tasks:
            self._drain_event.set()
        self._wake_owner()

    async def _cancel_active_tasks(self) -> None:
        if not self._active_tasks:
            return

        tasks = list(self._active_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _wait_for_drain(self) -> None:
        if self._active_tasks:
            await self._drain_event.wait()

    async def _execute_request(
        self, conn: MCPConnection, request: _CallToolRequest
    ) -> None:
        if request.result.done():
            return
        try:
            async with self._sem:
                if request.result.done():
                    return
                result = await conn.call_tool(request.tool_name, request.arguments)
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                if request.result.cancelled():
                    raise
                self._set_request_exception(
                    request, self._map_cancelled_request_exception(exc)
                )
                raise
            self._set_request_exception(request, exc)
        else:
            if not request.result.done():
                request.result.set_result(result)

    def _should_finish_close(self) -> bool:
        return (
            self._close_requested.is_set()
            and self._requests.empty()
            and not self._active_tasks
        )

    async def _run(self) -> None:
        try:
            async with self._conn_ctx as conn:
                tools_info = await asyncio.wait_for(
                    conn.list_tools(), timeout=MCP_CONNECT_TIMEOUT
                )
                self._ready.set_result(
                    _StartupState(connection=conn, tools_info=tools_info)
                )

                while True:
                    made_progress = False

                    while len(self._active_tasks) < self._max_inflight:
                        try:
                            request = self._requests.get_nowait()
                        except asyncio.QueueEmpty:
                            break

                        made_progress = True
                        if request is _CLOSE_REQUEST:
                            continue
                        if request.result.done():
                            continue

                        task = asyncio.create_task(self._execute_request(conn, request))
                        self._cancel_active_task_for_result(request, task)
                        self._track_active_task(task)

                    if self._should_finish_close():
                        return

                    if made_progress:
                        continue

                    self._owner_wakeup.clear()
                    if self._should_finish_close():
                        return
                    if (
                        len(self._active_tasks) < self._max_inflight
                        and not self._requests.empty()
                    ):
                        continue
                    if self._owner_wakeup.is_set():
                        continue
                    await self._owner_wakeup.wait()
        except BaseException as exc:
            self._fatal_error = exc
            if not self._ready.done():
                self._ready.set_exception(exc)
            else:
                self._fail_queued_requests(self._failure_for_owner_exit(exc))
            await self._cancel_active_tasks()
        finally:
            self._closed = True

    async def wait_ready(self, timeout: float) -> _StartupState:
        return await asyncio.wait_for(self._ready, timeout=timeout)

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if self._closing:
            raise ManagedConnClosing("MCP connection is closing")
        if self._fatal_error is not None:
            raise ManagedConnDead(
                "MCP connection is unavailable"
            ) from self._fatal_error
        if self._closed:
            raise ManagedConnClosing("MCP connection is closing")

        loop = asyncio.get_running_loop()
        result: asyncio.Future[Any] = loop.create_future()
        try:
            self._requests.put_nowait(
                _CallToolRequest(
                    tool_name=tool_name,
                    arguments=arguments,
                    result=result,
                )
            )
        except asyncio.QueueFull as exc:
            raise ManagedConnBackpressure("MCP pending request queue is full") from exc

        self._wake_owner()
        try:
            return await result
        except asyncio.CancelledError:
            if not result.done():
                result.cancel()
            self._wake_owner()
            raise

    async def close(self, timeout: float) -> None:
        self._closing = True
        self._close_requested.set()
        try:
            self._requests.put_nowait(_CLOSE_REQUEST)
        except asyncio.QueueFull:
            pass
        self._wake_owner()

        if self._task.done():
            try:
                await self._task
            except Exception:
                pass
            return

        try:
            await asyncio.wait_for(self._task, timeout=timeout)
        except TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        except Exception:
            pass
        finally:
            self._wake_owner()


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
        #    "has_calculation_preflight": bool}
        self.tools_by_server: dict[str, dict[str, dict[str, Any]]] = {}

        # Long-lived managed connections (enter/exit in same Task)
        self._managed: dict[str, _ManagedConn] = {}

        # Concurrency policy skeleton for Task 1.
        self.concurrency_defaults_by_transport: dict[str, MCPConcurrencyPolicy] = {}
        self.concurrency_by_server: dict[str, MCPConcurrencyPolicy] = {}
        self._server_transports: dict[str, str] = {}

        # In-flight startup tasks keyed by server.
        self._startup_tasks: dict[str, asyncio.Task[None]] = {}

        # 需要 calculation preflight 的 server 集合
        self.calculation_preflight_servers: set[str] = set()

        # calculation preflight 工厂函数
        self.calculation_preflight_factory: Callable[[], Any] | None = None

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
        self._closing = False

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
        if self._closing:
            raise RuntimeError("MCP manager is closing")
        if name in self.connections:
            return

        startup = self._startup_tasks.get(name)
        if startup is None:
            startup = asyncio.create_task(
                self._add_server_once(name, transport, **connection_kwargs)
            )
            self._startup_tasks[name] = startup

            def _remove_startup_task(
                done: asyncio.Task[None], *, server_name=name
            ) -> None:
                try:
                    done.exception()
                except asyncio.CancelledError:
                    pass
                if self._startup_tasks.get(server_name) is done:
                    self._startup_tasks.pop(server_name, None)

            startup.add_done_callback(_remove_startup_task)

        try:
            await asyncio.shield(startup)
        finally:
            if self._startup_tasks.get(name) is startup and startup.done():
                self._startup_tasks.pop(name, None)

    def _resolve_policy(self, server_name: str, transport: str) -> MCPConcurrencyPolicy:
        policy = self.concurrency_by_server.get(server_name)
        if policy is not None:
            return policy

        normalized_transport = transport.lower()
        policy = self.concurrency_defaults_by_transport.get(normalized_transport)
        if policy is not None:
            return policy

        policy = MCPConcurrencyPolicy.default_for_transport(normalized_transport)
        self.concurrency_defaults_by_transport[normalized_transport] = policy
        return policy

    async def _add_server_once(
        self, name: str, transport: str, **connection_kwargs: Any
    ) -> None:
        if name in self.connections:
            return

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
        policy = self._resolve_policy(name, transport)

        for attempt in range(1, _MAX_RETRIES + 1):
            managed: _ManagedConn | None = None
            try:
                if self._closing:
                    raise RuntimeError("MCP manager is closing")

                conn_ctx = create_connection(transport=transport, **connection_kwargs)
                managed = _ManagedConn(
                    conn_ctx,
                    max_inflight=policy.max_inflight,
                    max_pending_requests=policy.max_pending_requests,
                )
                self._managed[name] = managed
                self._server_transports[name] = transport

                # 带超时的连接与 list_tools（均在 managed owner task 内执行）
                startup = await managed.wait_ready(timeout=MCP_CONNECT_TIMEOUT)

                # 连接成功
                self.connections[name] = startup.connection

                logger.info(
                    "Found %d tools from MCP server '%s'",
                    len(startup.tools_info),
                    name,
                )
                self._build_tools(name, startup.connection, startup.tools_info)
                logger.info("Successfully added MCP server '%s'", name)
                return

            except asyncio.CancelledError:
                if managed is not None:
                    await managed.close(timeout=_PER_CONN_SHUTDOWN_TIMEOUT)
                    self._managed.pop(name, None)
                    self._server_transports.pop(name, None)
                raise
            except _retry_exc as e:
                last_error = e
                if managed is not None:
                    await managed.close(timeout=_PER_CONN_SHUTDOWN_TIMEOUT)
                    self._managed.pop(name, None)
                    self._server_transports.pop(name, None)
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
            except Exception:
                if managed is not None:
                    await managed.close(timeout=_PER_CONN_SHUTDOWN_TIMEOUT)
                    self._managed.pop(name, None)
                    self._server_transports.pop(name, None)
                raise

        # 所有重试失败
        raise RuntimeError(
            f"Failed to connect MCP server '{name}' after {_MAX_RETRIES} attempts"
        ) from last_error

    async def call_tool(
        self, server_name: str, remote_tool_name: str, arguments: dict[str, Any]
    ) -> Any:
        if self._closing:
            raise ManagedConnClosing("MCP manager is closing")

        startup = self._startup_tasks.get(server_name)
        if startup is not None:
            try:
                await asyncio.shield(startup)
            except asyncio.CancelledError as exc:
                if self._closing:
                    raise ManagedConnClosing("MCP manager is closing") from exc
                raise

        managed = self._managed.get(server_name)
        if managed is None:
            raise ValueError(f"MCP server '{server_name}' is not connected")

        return await managed.call_tool(remote_tool_name, arguments)

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
        5. calculation_preflight 注入标记
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
            tools_info = [t for t in tools_info if t.get("name") in include_only]
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
                    t.get("name", "").startswith("submit_")
                    and t.get("name", "")[len("submit_") :] in sync_tools
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
            tool_name = tool_info.get("name", "")
            desc = tool_info.get("description", "")
            if not tool_name.startswith("submit_") and desc:
                base_descriptions[tool_name] = desc

        # 3. async 去重：当 submit_X 存在时移除 base X
        submit_names = {
            t.get("name", "")[len("submit_") :]
            for t in tools_info
            if t.get("name", "").startswith("submit_")
        }
        if submit_names:
            before_dedup = len(tools_info)
            tools_info = [
                t
                for t in tools_info
                if t.get("name", "").startswith("submit_")
                or t.get("name", "") not in submit_names
            ]
            if len(tools_info) < before_dedup:
                logger.info(
                    "Dedup: removed %d base tools with submit_* counterparts "
                    "on server '%s'",
                    before_dedup - len(tools_info),
                    name,
                )

        # 判断是否需要 calculation_preflight
        needs_calculation_preflight = (
            self.calculation_preflight_servers
            and self.calculation_preflight_factory
            and name in self.calculation_preflight_servers
        )

        # 构建工具信息字典
        server_tools: dict[str, dict[str, Any]] = {}
        for tool_info in tools_info:
            original_name = tool_info["name"]
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
            description = tool_info.get("description", "")
            if original_name.startswith("submit_"):
                base_name = original_name[len("submit_") :]
                base_desc = base_descriptions.get(base_name, "")
                if base_desc and len(base_desc) > len(description):
                    description = base_desc

            tool_dict: dict[str, Any] = {
                "name": prefixed_name,
                "description": description,
                "input_schema": tool_info.get("input_schema", {}),
                "remote_tool_name": original_name,
                "connection": conn,
                "has_calculation_preflight": bool(needs_calculation_preflight),
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
        self._closing = True

        startup_tasks = list(self._startup_tasks.values())
        for task in startup_tasks:
            task.cancel()

        for name, managed in list(self._managed.items()):
            try:
                await managed.close(timeout=_PER_CONN_SHUTDOWN_TIMEOUT)
                logger.debug("Closed MCP connection: %s", name)
            except Exception as e:
                logger.warning("Error closing MCP connection '%s': %s", name, e)

        if startup_tasks:
            await asyncio.gather(*startup_tasks, return_exceptions=True)

        self.connections.clear()
        self.tools_by_server.clear()
        self._managed.clear()
        self._startup_tasks.clear()
        self._seen_tools.clear()
        self._server_transports.clear()

        logger.info("MCP cleanup complete")
