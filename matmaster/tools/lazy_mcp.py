"""Lazy MCP tool loading via per-server owner-task routing."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext
from matmaster.types.topology import ToolPlane

logger = logging.getLogger(__name__)

_DEFAULT_MCP_TOOL_TIMEOUT = 120.0
_DEFAULT_LAZY_MCP_CONNECT_TIMEOUT = 5.0
_DEFAULT_CALCULATION_SYNC_MCP_TOOL_TIMEOUT = 10.0

# Shutdown budgets -- semantically separate from connect/execution timeouts.
_MCP_CONNECTOR_SHUTDOWN_TIMEOUT = 5.0  # overall connector cleanup budget (seconds)
_MCP_LOOP_JOIN_TIMEOUT = 1.0  # thread join after loop.stop() (seconds)


def _parse_claims(raw_claims: Any) -> tuple[ResourceClaim, ...]:
    claims: list[ResourceClaim] = []
    for raw in raw_claims or ():
        if isinstance(raw, ResourceClaim):
            claims.append(raw)
        elif isinstance(raw, dict):
            claims.append(ResourceClaim(**raw))
    return tuple(claims)


class LazyMCPTool:
    """Placeholder MCP tool that delegates runtime work to LazyMCPConnector."""

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        remote_tool_name: str,
        description: str,
        input_schema: dict,
        connector: Any,
        runtime_meta: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> None:
        self._name = tool_name
        self._static_description = description
        self._input_schema = input_schema
        self._server_name = server_name
        self._remote_tool_name = remote_tool_name
        self._connector = connector

        meta = runtime_meta or {}
        self._plane = (
            ToolPlane(meta["plane"])
            if meta.get("plane")
            else ToolPlane.EXTERNAL_SERVICE
        )
        self._effect_level: str = meta.get("effect_level", "external_effect")
        self._capabilities = frozenset(meta.get("capabilities", ()))
        self._fast_path_eligible = bool(meta.get("fast_path_eligible", False))
        self._exposed_to_model = meta.get("exposed_to_model", True)
        self._resource_claims = _parse_claims(meta.get("resource_claims", ()))
        self._max_result_chars = int(meta.get("max_result_chars", 0) or 0)
        self._stop_mode = meta.get("stop_mode", "best_effort")
        self._state_mode = meta.get("state_mode", "stateless")
        if meta.get("timeout") is not None:
            self._timeout = float(meta["timeout"])
        elif timeout is not None:
            self._timeout = float(timeout)
        else:
            self._timeout = _DEFAULT_MCP_TOOL_TIMEOUT

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._static_description

    def describe(self, ctx: ToolDescriptionContext | None = None) -> str:
        return self._static_description

    def prompt(self, ctx: ToolDescriptionContext | None = None) -> str | None:
        return None

    @property
    def json_schema(self) -> dict[str, Any]:
        return self._input_schema

    @property
    def resource_claims(self) -> tuple[ResourceClaim, ...]:
        return self._resource_claims

    @property
    def capabilities(self) -> frozenset[str]:
        return self._capabilities

    @property
    def effect_level(self) -> str:
        return self._effect_level

    @property
    def fast_path_eligible(self) -> bool:
        return self._fast_path_eligible

    @property
    def max_result_chars(self) -> int:
        return self._max_result_chars

    @property
    def plane(self) -> ToolPlane:
        return self._plane

    @property
    def state_mode(self) -> str:
        return self._state_mode

    @property
    def stop_mode(self) -> str:
        return self._stop_mode

    @property
    def exposed_to_model(self) -> bool:
        return self._exposed_to_model

    async def _do_call(self, arguments: dict[str, Any]) -> ToolResult:
        """Raw MCP call: resolve args + actor-routed call_tool + format."""
        resolved_args = arguments
        path_adaptor = await self._connector.get_path_adaptor(self._server_name)
        if path_adaptor:
            try:
                resolved_args = path_adaptor.resolve_args(
                    workspace_path=self._connector.workspace_path,
                    args=arguments,
                    tool_name=self._name,
                    server_name=self._server_name,
                    tool_description=self._static_description,
                    input_schema=self._input_schema,
                    session=getattr(self._connector, "session", None),
                )
            except Exception as e:
                logger.warning("path_adaptor resolve_args failed: %s", e)

        try:
            result_content = await self._connector.call_tool(
                self._server_name,
                self._remote_tool_name,
                resolved_args,
            )
            content = self._format_result(result_content)
            return ToolResult(status="success", content=content)
        except RuntimeError as e:
            # MCPConnection.call_tool raises RuntimeError on isError=True
            return ToolResult(status="error", content=str(e))

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return await self._do_call(arguments)

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> ToolResult:
        cancel_token = getattr(exec_ctx, "cancel_token", None) if exec_ctx else None

        if cancel_token is not None and cancel_token.is_cancelled:
            return self._cancelled_result()

        call_coro = asyncio.wait_for(self._do_call(arguments), timeout=self._timeout)

        if cancel_token is None:
            try:
                return await call_coro
            except asyncio.TimeoutError:
                return self._timeout_result()

        call_task = asyncio.create_task(call_coro)
        stop_task = asyncio.create_task(cancel_token.wait_async())
        done, pending = await asyncio.wait(
            {call_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )

        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        if call_task in done:
            try:
                return call_task.result()
            except asyncio.TimeoutError:
                return self._timeout_result()

        return self._cancelled_result()

    def _timeout_result(self) -> ToolResult:
        timeout_text = f"{self._timeout:g}"
        return ToolResult(
            status="timeout",
            content=f"MCP tool {self._name} timed out after {timeout_text}s",
            meta={"layer": "tool"},
        )

    def _cancelled_result(self) -> ToolResult:
        if self._stop_mode == "cancellable":
            return ToolResult(status="cancelled", content="Run cancelled.")
        return ToolResult(
            status="cancelled",
            content=(
                "Cancellation requested (best-effort). "
                "Tool may have partially completed."
            ),
        )

    def _format_result(self, result_content: list) -> str:
        """Format MCPConnection.call_tool result content list to string.

        Handles MCP SDK content items (TextContent with .text attribute,
        or plain dict with 'text' key).
        """
        parts: list[str] = []
        for item in result_content:
            if hasattr(item, 'text'):
                parts.append(item.text)
            elif isinstance(item, dict) and 'text' in item:
                parts.append(item['text'])
            else:
                parts.append(str(item))

        if not parts:
            return ''
        if len(parts) == 1:
            text = parts[0].strip()
            if text.startswith('{') or text.startswith('['):
                try:
                    parsed = json.loads(text)
                    return json.dumps(parsed, ensure_ascii=False, default=str)
                except json.JSONDecodeError:
                    return text
            return text
        return '\n'.join(parts)


def configure_mcp_manager(
    manager: Any,
    mcp_config: dict,
    all_server_names: set[str] | None = None,
) -> None:
    """Inject MatMaster domain-specific config into MCPToolManager.

    Extracted from playground._setup_mcp_tools() for shared use by
    LazyMCPConnector and the old playground path. Behavior-preserving:
    matches the original playground code exactly.

    Args:
        manager: MCPToolManager instance
        mcp_config: Full mcp section from config.yaml
        all_server_names: All known server names (for fallback when
            calculation_servers is absent). Playground passes parsed
            server names; LazyMCPConnector passes server_config keys.
    """
    if mcp_config.get("path_adaptor") == "calculation":
        calc_servers = mcp_config.get("calculation_servers")
        if calc_servers:
            manager.path_adaptor_servers = set(calc_servers)
        elif all_server_names:
            manager.path_adaptor_servers = set(all_server_names)
        try:
            from matmaster.adaptors.calculation import get_calculation_path_adaptor

            manager.path_adaptor_factory = lambda: get_calculation_path_adaptor(
                mcp_config
            )
        except ImportError:
            logger.warning(
                "matmaster.adaptors.calculation not available, skipping path_adaptor"
            )

        executors = mcp_config.get("calculation_executors") or {}
        manager.sync_tools_by_server = {
            name: set(cfg.get("sync_tools") or [])
            for name, cfg in executors.items()
            if isinstance(cfg, dict) and cfg.get("sync_tools")
        }

    include_only = mcp_config.get("tool_include_only")
    if include_only and isinstance(include_only, dict):
        manager.tool_include_only = {
            k: list(v) if isinstance(v, (list, tuple)) else []
            for k, v in include_only.items()
        }


def resolve_lazy_mcp_tool_timeout(
    mcp_config: dict[str, Any],
    *,
    server_name: str,
    remote_tool_name: str,
) -> float | None:
    """Resolve LazyMCP call timeout from runtime config."""
    tool_timeouts = mcp_config.get('tool_timeouts', {})
    if isinstance(tool_timeouts, dict):
        server_timeout = tool_timeouts.get(server_name)
        if server_timeout is not None:
            return float(server_timeout)

    executors = mcp_config.get('calculation_executors') or {}
    server_cfg = executors.get(server_name)
    if not isinstance(server_cfg, dict):
        return None
    if not server_cfg.get('executor'):
        return None

    sync_tools = set(server_cfg.get('sync_tools') or [])
    if remote_tool_name in sync_tools:
        return _DEFAULT_CALCULATION_SYNC_MCP_TOOL_TIMEOUT
    return None


class LazyMCPConnector:
    """On-demand MCP server connector with background event loop.

    Creates a background asyncio event loop thread on first connect.
    Applies domain-specific config via configure_mcp_manager().
    Routes requests to per-server owner tasks on that loop.
    """

    def __init__(
        self,
        mcp_server_config: dict,
        mcp_config: dict,
        session: Any = None,
        workspace_path: str = "",
        connect_timeout: float | None = None,
    ) -> None:
        self._server_config = mcp_server_config
        self._mcp_config = mcp_config
        self._manager: Any | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._path_adaptors: dict[str, Any] = {}
        self._closing = False
        self.session = session
        self.workspace_path = workspace_path
        configured_connect_timeout = (
            mcp_config.get("lazy_connect_timeout")
            if isinstance(mcp_config, dict)
            else None
        )
        if connect_timeout is not None:
            self._connect_timeout = float(connect_timeout)
        elif configured_connect_timeout is not None:
            self._connect_timeout = float(configured_connect_timeout)
        else:
            self._connect_timeout = _DEFAULT_LAZY_MCP_CONNECT_TIMEOUT

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None and not self._loop.is_closed():
            return self._loop
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="lazy-mcp-loop"
        )
        self._loop_thread.start()
        return self._loop

    def _ensure_manager(self) -> Any:
        if self._manager is not None:
            return self._manager
        from matmaster.mcp.manager import MCPToolManager

        loop = self._ensure_loop()
        self._manager = MCPToolManager()
        self._manager.loop = loop
        configure_mcp_manager(
            self._manager,
            self._mcp_config,
            all_server_names=set(self._server_config.keys()),
        )
        return self._manager

    async def _await_on_manager_loop(
        self,
        coro: Any,
        *,
        timeout: float | None = None,
    ) -> Any:
        manager = self._ensure_manager()
        fut = asyncio.run_coroutine_threadsafe(coro, manager.loop)
        wrapped = asyncio.wrap_future(fut)
        wrapped = asyncio.shield(wrapped)
        if timeout is not None:
            return await asyncio.wait_for(wrapped, timeout=timeout)
        return await wrapped

    async def ensure_actor(self, server_name: str) -> None:
        if self._closing:
            raise RuntimeError("LazyMCPConnector is closing")

        manager = self._ensure_manager()
        if server_name in manager.connections:
            return

        server_cfg = self._server_config.get(server_name)
        if not server_cfg:
            raise ValueError(f"MCP server '{server_name}' not in config")

        await self._await_on_manager_loop(
            manager.add_server(name=server_name, **server_cfg),
            timeout=self._connect_timeout,
        )

    async def get_path_adaptor(self, server_name: str) -> Any | None:
        manager = self._ensure_manager()
        if not (
            manager.path_adaptor_factory and server_name in manager.path_adaptor_servers
        ):
            return None

        if server_name not in self._path_adaptors:
            self._path_adaptors[server_name] = manager.path_adaptor_factory()
        return self._path_adaptors[server_name]

    async def call_tool(
        self,
        server_name: str,
        remote_tool_name: str,
        arguments: dict[str, Any],
    ) -> list[Any]:
        if self._closing:
            raise RuntimeError("LazyMCPConnector is closing")

        await self.ensure_actor(server_name)
        manager = self._ensure_manager()
        return await self._await_on_manager_loop(
            manager.call_tool(server_name, remote_tool_name, arguments)
        )

    async def ensure_connection(self, server_name: str) -> dict[str, Any]:
        """Compatibility shim for legacy tests; avoid exposing raw connection use."""
        await self.ensure_actor(server_name)
        path_adaptor = await self.get_path_adaptor(server_name)
        return {"path_adaptor": path_adaptor}

    def connect_and_get_tool(self, server_name: str, remote_tool_name: str) -> Any:
        """Legacy sync method -- kept for backward compatibility.

        Prefer call_tool() for the new async runtime path.
        """
        manager = self._ensure_manager()

        if server_name not in manager.connections:
            server_cfg = self._server_config.get(server_name)
            if not server_cfg:
                raise ValueError(f"MCP server '{server_name}' not in config")
            fut = asyncio.run_coroutine_threadsafe(
                manager.add_server(name=server_name, **server_cfg),
                manager.loop,
            )
            fut.result(timeout=self._connect_timeout)

        return manager.tools_by_server[server_name][f"{server_name}_{remote_tool_name}"]

    async def cleanup(self) -> None:
        try:
            self._closing = True
            if self._manager and self._loop and not self._loop.is_closed():
                try:
                    fut = asyncio.run_coroutine_threadsafe(
                        self._manager.cleanup(), self._loop
                    )
                    await asyncio.wait_for(
                        asyncio.wrap_future(fut),
                        timeout=_MCP_CONNECTOR_SHUTDOWN_TIMEOUT,
                    )
                except Exception as e:
                    logger.warning("LazyMCPConnector cleanup error: %s", e)
        finally:
            # Guarantee loop stop + thread join even on CancelledError.
            if self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._loop.stop)
            if self._loop_thread:
                self._loop_thread.join(timeout=_MCP_LOOP_JOIN_TIMEOUT)
            self._path_adaptors.clear()
            self._manager = None
            self._loop = None
            self._loop_thread = None
            self._closing = False
