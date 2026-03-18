"""EvoMaster Agent Tools 模块

提供可扩展的工具系统，支持 Agent 调用各种工具完成任务。

目录结构：
- base.py: 工具基类和注册中心
- builtin/: 内置工具（Bash, Editor, Think, Finish）
- mcp/: MCP 协议工具支持
"""

from __future__ import annotations

from .base import (
    BaseTool,
    ToolError,
    ToolRegistry,
    create_default_registry,
    create_registry,
)

# 内置工具
from .builtin import (
    BashTool,
    BashToolParams,
    EditorTool,
    EditorToolParams,
    FinishTool,
    FinishToolParams,
    ThinkTool,
    ThinkToolParams,
)

# MCP 工具
from .mcp import (
    MCPConnection,
    MCPTool,
    MCPToolManager,
    create_connection,
)
from .skill import SkillTool, SkillToolParams

__all__ = [
    # Base
    'BaseTool',
    'ToolRegistry',
    'ToolError',
    'create_default_registry',
    'create_registry',
    # Builtin Tools
    'BashTool',
    'BashToolParams',
    'EditorTool',
    'EditorToolParams',
    'ThinkTool',
    'ThinkToolParams',
    'FinishTool',
    'FinishToolParams',
    # Skill Tools
    'SkillTool',
    'SkillToolParams',
    # MCP Tools
    'MCPTool',
    'MCPToolManager',
    'MCPConnection',
    'create_connection',
]
