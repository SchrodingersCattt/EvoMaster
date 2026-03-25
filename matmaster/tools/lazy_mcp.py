"""Lazy MCP tool loading -- placeholder tools + on-demand connector.

LazyMCPTool satisfies the matmaster Tool Protocol using cached schemas.
On first execute(), it connects to the MCP server via LazyMCPConnector.
"""
from __future__ import annotations

import json
import logging
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
