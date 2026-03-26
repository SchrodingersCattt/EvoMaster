"""ToolResult model and compatibility normalization for tool execution."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """Structured tool execution result consumed by the kernel and SSE layer."""

    status: str = "success"
    content: str = ""
    info: dict[str, Any] = Field(default_factory=dict)


def normalize_tool_result(raw: str | ToolResult | None) -> ToolResult:
    """Convert legacy tool return values into ToolResult."""
    if isinstance(raw, ToolResult):
        return raw
    if raw is None:
        return ToolResult()

    content = str(raw)
    status = "error" if content.lstrip().startswith("Error:") else "success"
    return ToolResult(status=status, content=content)
