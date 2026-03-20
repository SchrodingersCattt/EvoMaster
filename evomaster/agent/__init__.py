"""EvoMaster Agent 模块

Agent 是 EvoMaster 的智能体组件，包含：
- 类型定义（Message, Dialog, Trajectory）- 从 utils 导入
- 上下文管理（ContextManager）
- Agent 基类和实现
- Session（与 Env 交互的介质）
- Tools（工具系统）
"""

from __future__ import annotations

from evomaster.utils.types import (
    AssistantMessage,
    Dialog,
    FunctionCall,
    FunctionSpec,
    Message,
    MessageRole,
    StepRecord,
    SystemMessage,
    TaskInstance,
    ToolCall,
    ToolMessage,
    ToolSpec,
    Trajectory,
    UserMessage,
)

from .agent import Agent, AgentConfig, BaseAgent
from .context import ContextConfig, ContextManager, TruncationStrategy

# Session 子模块
from .session import (
    BaseSession,
    DockerSession,
    DockerSessionConfig,
    SessionConfig,
)

# Tools 子模块
from .tools import (
    BaseTool,
    BashTool,
    EditorTool,
    FinishTool,
    ToolError,
    ToolRegistry,
    create_default_registry,
    create_registry,
)

__all__ = [
    # Types
    'Message',
    'MessageRole',
    'SystemMessage',
    'UserMessage',
    'AssistantMessage',
    'ToolMessage',
    'ToolCall',
    'FunctionCall',
    'ToolSpec',
    'FunctionSpec',
    'Dialog',
    'StepRecord',
    'Trajectory',
    'TaskInstance',
    # Context
    'ContextManager',
    'ContextConfig',
    'TruncationStrategy',
    # Agent
    'BaseAgent',
    'Agent',
    'AgentConfig',
    # Session
    'BaseSession',
    'SessionConfig',
    'DockerSession',
    'DockerSessionConfig',
    # Tools
    'BaseTool',
    'ToolRegistry',
    'ToolError',
    'create_default_registry',
    'create_registry',
    'BashTool',
    'EditorTool',
    'FinishTool',
]
