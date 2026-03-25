"""Tool Protocol and ToolRegistry -- unified tool management for the assembly layer.

Tool is the @runtime_checkable Protocol each tool must satisfy (name, description,
json_schema, execute). ToolRegistry provides flat-namespace registration with source
tags (builtin/mcp/skill), same-name override with warning, execute dispatch, and
OpenAI function calling format definitions.

Consumed by AgentKernel via AgentRuntimeSpec.tool_registry.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

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

    def execute(self, arguments: dict[str, Any]) -> str: ...


class ToolRegistry:
    """Flat-namespace tool registry with source tracking.

    Registration order determines override: assemble() registers
    builtin -> MCP -> skill, so skill tools take final precedence.
    Same-name registration overwrites the previous entry with a warning log.
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

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """Dispatch execution to the named tool.

        Returns error string if tool name not found, listing available tools.
        """
        tool = self._tools.get(name)
        if tool is None:
            available = ", ".join(sorted(self._tools))
            return f"Error: Tool '{name}' not found. Available: {available}"
        return tool.execute(arguments)

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return tool definitions in OpenAI function calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.json_schema,
                },
            }
            for t in self._tools.values()
        ]

    def get_tools_by_source(self, source: str) -> list[Tool]:
        """Return tools registered under the given source label."""
        return [
            self._tools[name]
            for name, s in self._sources.items()
            if s == source
        ]

    @property
    def all_tools(self) -> list[Tool]:
        """Return all registered Tool instances."""
        return list(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:  # type: ignore[override]
        return name in self._tools
