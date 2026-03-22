"""ToolRegistry and Tool Protocol for tool management.

Tool is the interface each tool must implement.
ToolRegistry manages registered tools and provides tool definitions
for LLM API calls.

NOTE: This is a minimal stub created by Plan 02 to unblock ContextBuilder.
Plan 01 provides the full implementation with registration, lookup,
and definition generation.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Tool(Protocol):
    """Tool interface: name, description, schema, execute."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def json_schema(self) -> dict[str, Any]: ...

    def execute(self, arguments: dict[str, Any]) -> str: ...


class ToolRegistry:
    """Registry for managing tools.

    Provides registration, lookup by source, and tool definition
    generation for LLM API calls.
    """

    def __init__(self) -> None:
        self._tools: list[tuple[Tool, str]] = []

    def register(self, tool: Tool, *, source: str = "unknown") -> None:
        """Register a tool with its source label."""
        self._tools.append((tool, source))

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return tool definitions formatted for LLM API calls."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.json_schema,
                },
            }
            for t, _ in self._tools
        ]

    def get_tools_by_source(self, source: str) -> list[Tool]:
        """Return tools filtered by source label."""
        return [t for t, s in self._tools if s == source]

    @property
    def all_tools(self) -> list[Tool]:
        """Return all registered tools."""
        return [t for t, _ in self._tools]
