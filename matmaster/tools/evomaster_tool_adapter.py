"""EvoToolAdapter -- adapts EvoMaster BaseTool to the matmaster Tool Protocol.

Wraps an EvoMaster BaseTool instance (SkillTool, MCPTool, or any custom tool)
together with a bound session, exposing the matmaster Tool Protocol interface
(name, description, json_schema, execute).  This allows EvoMaster tools to be
registered in the matmaster ToolRegistry without weakening the Protocol contract.

The adapter serializes arguments to JSON for the EvoMaster execute() call and
normalizes the observation to a plain string.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from matmaster.tools.tool_result import ToolResult

if TYPE_CHECKING:
    from evomaster.agent.tools.base import BaseTool


class EvoToolAdapter:
    """Adapter from EvoMaster BaseTool to matmaster Tool Protocol.

    Satisfies the matmaster ``Tool`` Protocol:
      - ``name``        -> ``tool.name``
      - ``description`` -> ``tool.params_class.__doc__`` (stripped)
      - ``json_schema`` -> ``tool.params_class.model_json_schema()``
      - ``execute(arguments)`` -> serializes args, delegates to
        ``tool.execute(session, args_json)`` and normalizes the
        observation to a string.
    """

    def __init__(self, tool: BaseTool, session: Any) -> None:
        self._tool = tool
        self._session = session

    @property
    def name(self) -> str:
        return self._tool.name

    @property
    def description(self) -> str:
        return (self._tool.params_class.__doc__ or "").strip()

    @property
    def json_schema(self) -> dict[str, Any]:
        return self._tool.params_class.model_json_schema()

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return await asyncio.to_thread(self._execute_sync, arguments)

    def _execute_sync(self, arguments: dict[str, Any]) -> ToolResult:
        """Synchronous execution body -- wrapped by execute() via to_thread."""
        args_json = json.dumps(arguments, ensure_ascii=False)
        observation, info = self._tool.execute(self._session, args_json)
        content = (
            observation
            if isinstance(observation, str)
            else json.dumps(observation, ensure_ascii=False, default=str)
        )
        info_dict = info if isinstance(info, dict) else {}
        status = (
            "error"
            if "error" in info_dict or content.lstrip().startswith("Error:")
            else "success"
        )
        return ToolResult(status=status, content=content, info=info_dict)
