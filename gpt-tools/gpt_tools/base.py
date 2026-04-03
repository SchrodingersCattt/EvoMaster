"""Base classes for standalone GPT-style tools."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from .context import ToolContext
from .models import ToolDefinition, ToolResult, normalize_tool_result


class BaseTool(ABC):
    """Common contract for standalone tool implementations."""

    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[dict[str, Any]]
    strict: ClassVar[bool] = True
    defer_loading: ClassVar[bool] = False
    search_hint: ClassVar[str] = ""

    def __init__(self, context: ToolContext) -> None:
        self.context = context
        self.logger = logging.getLogger(self.__class__.__name__)

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            strict=self.strict,
            defer_loading=self.defer_loading,
            search_hint=self.search_hint,
        )

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            self._validate_arguments(arguments)
            raw = await asyncio.to_thread(self._execute, arguments)
            return normalize_tool_result(raw)
        except Exception as exc:
            self.logger.error("Tool %s failed: %s", self.name, exc, exc_info=True)
            return ToolResult.error(f"Error: {exc}")

    def _validate_arguments(self, arguments: dict[str, Any]) -> None:
        schema = self.input_schema
        required = set(schema.get("required", []))
        missing = sorted(key for key in required if key not in arguments)
        if missing:
            raise ValueError(f"Missing required arguments: {', '.join(missing)}")

        properties = set(schema.get("properties", {}).keys())
        if schema.get("additionalProperties") is False:
            unknown = sorted(key for key in arguments if key not in properties)
            if unknown:
                raise ValueError(f"Unknown arguments: {', '.join(unknown)}")

    @abstractmethod
    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        """Run the tool synchronously."""

