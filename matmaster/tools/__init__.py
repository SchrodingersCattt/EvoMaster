"""matmaster.tools -- Tool registration and adaptation.

Provides ToolRegistry for unified tool management and EvoToolAdapter
for bridging EvoMaster BaseTool to the matmaster Tool Protocol.
"""

from .evomaster_tool_adapter import EvoToolAdapter
from .tool_registry import Tool, ToolRegistry

__all__ = [
    "EvoToolAdapter",
    "Tool",
    "ToolRegistry",
]
