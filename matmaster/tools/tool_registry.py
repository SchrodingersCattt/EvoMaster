"""Tool Protocol and ToolRegistry -- pure storage layer for the assembly layer.

Tool is the @runtime_checkable Protocol each tool must satisfy (name, description,
json_schema, execute). ToolRegistry provides flat-namespace registration with source
tags (builtin/mcp/skill) and same-name override with warning.

Upper-layer operations (execute dispatch, OpenAI definitions, stop_event injection)
are handled by ToolCatalog and FullToolRunner. Registry is consumed only as a storage
backend via ToolCatalog.registry.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from matmaster.tools.tool_result import ToolResult

logger = logging.getLogger(__name__)


@runtime_checkable
class Tool(Protocol):
    """Tool interface: name, description, schema, execute.

    Every tool source (builtin, MCP, skill) wraps its tool into a class
    satisfying this Protocol. The kernel sees only this unified interface.
    """

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def json_schema(self) -> dict[str, Any]: ...

    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult | None: ...


class ToolRegistry:
    """Pure storage: flat-namespace tool registry with source tracking.

    Registration order determines override: assemble() registers
    builtin -> MCP -> skill, so skill tools take final precedence.
    Same-name registration overwrites the previous entry with a warning log.

    Upper-layer operations (execute, definitions, stop_event) live in
    ToolCatalog and FullToolRunner -- not here.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._sources: dict[str, str] = {}

    def register(self, tool: Tool, *, source: str = "unknown") -> None:
        """Register a tool. Overwrites existing same-name tool with warning."""
        if tool.name in self._tools:
            old_source = self._sources[tool.name]
            logger.warning(
                "Tool '%s' overwritten: source '%s' -> '%s'",
                tool.name,
                old_source,
                source,
            )
        self._tools[tool.name] = tool
        self._sources[tool.name] = source

    @property
    def all_tools(self) -> list[Tool]:
        """Return all registered Tool instances."""
        return list(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:  # type: ignore[override]
        return name in self._tools
