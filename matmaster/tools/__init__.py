"""matmaster.tools -- Tool registration and execution.

Provides ToolRegistry for unified tool management. All tools satisfy
the Tool Protocol (name/description/json_schema/execute).
"""

from .tool_registry import Tool, ToolRegistry

__all__ = [
    "Tool",
    "ToolRegistry",
]
