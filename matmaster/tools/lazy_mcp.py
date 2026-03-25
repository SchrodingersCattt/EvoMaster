"""Lazy MCP tool loading -- placeholder tools + on-demand connector.

LazyMCPTool satisfies the matmaster Tool Protocol using cached schemas.
On first execute(), it connects to the MCP server via LazyMCPConnector.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from evomaster.agent.tools.mcp.mcp import MCPTool
    from evomaster.agent.tools.mcp.mcp_manager import MCPToolManager


class LazyMCPTool:
    """Placeholder MCP tool -- holds cached schema, connects on first execute.

    Implements matmaster Tool Protocol (name, description, json_schema, execute).
    Can be registered directly into ToolRegistry without EvoToolAdapter.
    """

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        remote_tool_name: str,
        description: str,
        input_schema: dict,
        connector: Any,
    ) -> None:
        self._name = tool_name
        self._description = description
        self._input_schema = input_schema
        self._server_name = server_name
        self._remote_tool_name = remote_tool_name
        self._connector = connector
        self._real_tool: MCPTool | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def json_schema(self) -> dict[str, Any]:
        return self._input_schema

    def execute(self, arguments: dict[str, Any]) -> str:
        if self._real_tool is None:
            self._real_tool = self._connector.connect_and_get_tool(
                self._server_name, self._remote_tool_name
            )
        args_json = json.dumps(arguments)
        observation, _info = self._real_tool.execute(
            self._connector.session, args_json
        )
        if isinstance(observation, str):
            return observation
        return json.dumps(observation, default=str)


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
            from evomaster.adaptors.calculation import get_calculation_path_adaptor

            manager.path_adaptor_factory = lambda: get_calculation_path_adaptor(
                mcp_config
            )
        except ImportError:
            logger.warning("evomaster.adaptors.calculation not available, skipping path_adaptor")

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


class LazyMCPConnector:
    """On-demand MCP server connector with background event loop.

    Creates a background asyncio event loop thread on first connect.
    Applies domain-specific config via configure_mcp_manager().
    """

    def __init__(
        self,
        mcp_server_config: dict,
        mcp_config: dict,
        session: Any = None,
    ) -> None:
        self._server_config = mcp_server_config
        self._mcp_config = mcp_config
        self._manager: MCPToolManager | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self.session = session

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
        from evomaster.agent.tools.mcp.mcp_manager import MCPToolManager

        loop = self._ensure_loop()
        self._manager = MCPToolManager()
        self._manager.loop = loop
        configure_mcp_manager(
            self._manager,
            self._mcp_config,
            all_server_names=set(self._server_config.keys()),
        )
        return self._manager

    def connect_and_get_tool(self, server_name: str, remote_tool_name: str) -> Any:
        manager = self._ensure_manager()

        if server_name not in manager.connections:
            server_cfg = self._server_config.get(server_name)
            if not server_cfg:
                raise ValueError(f"MCP server '{server_name}' not in config")
            fut = asyncio.run_coroutine_threadsafe(
                manager.add_server(name=server_name, **server_cfg),
                manager.loop,
            )
            fut.result(timeout=60)

        return manager.tools_by_server[server_name][
            f"{server_name}_{remote_tool_name}"
        ]

    def cleanup(self) -> None:
        if self._manager and self._loop and not self._loop.is_closed():
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._manager.cleanup(), self._loop
                )
                fut.result(timeout=30)
            except Exception as e:
                logger.warning("LazyMCPConnector cleanup error: %s", e)
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread:
            self._loop_thread.join(timeout=5)
