"""matmaster MCP 子系统

提供 MCP 连接管理和工具管理的原生实现，不依赖 evomaster。
"""

from .connection import MCPConnection, create_connection
from .manager import MCPToolManager

__all__ = ["MCPConnection", "create_connection", "MCPToolManager"]
