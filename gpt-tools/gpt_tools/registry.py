"""Registry and factory helpers for standalone GPT-style tools."""

from __future__ import annotations

from typing import Any

from .base import BaseTool
from .context import ToolContext
from .models import ToolDefinition, ToolResult


class ToolRegistry:
    """Minimal registry for standalone tools."""

    def __init__(self, context: ToolContext) -> None:
        self.context = context
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            available = ", ".join(sorted(self._tools))
            return ToolResult.error(
                f"Error: Tool '{name}' not found. Available tools: {available}"
            )
        return await tool.execute(arguments)

    def definitions(self) -> list[ToolDefinition]:
        return [tool.definition() for tool in self._tools.values()]

    def deferred_definitions(self) -> list[ToolDefinition]:
        return [definition for definition in self.definitions() if definition.defer_loading]


def build_reference_registry(context: ToolContext) -> ToolRegistry:
    """Build a standalone registry mirroring the reference tool set."""

    from .tools.agentic import AgentTool, SendMessageTool
    from .tools.filesystem import EditTool, ReadTool, WriteTool
    from .tools.interaction import AskUserQuestionTool, SkillTool, TodoWriteTool
    from .tools.shell_search import BashTool, GlobTool, GrepTool, ToolSearchTool
    from .tools.web import WebFetchTool, WebSearchTool

    registry = ToolRegistry(context)
    for tool in (
        ReadTool(context),
        EditTool(context),
        WriteTool(context),
        BashTool(context),
        GlobTool(context),
        GrepTool(context),
        WebFetchTool(context),
        WebSearchTool(context),
        AgentTool(context),
        TodoWriteTool(context),
        ToolSearchTool(context),
        SkillTool(context),
        AskUserQuestionTool(context),
        SendMessageTool(context),
    ):
        registry.register(tool)
    context.registry = registry
    return registry
