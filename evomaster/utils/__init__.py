"""EvoMaster Utils 模块

工具类和辅助函数，包括：
- LLM 接口封装
- 基础类型定义
- 其他通用工具
"""

from __future__ import annotations

from .llm import (
    AnthropicLLM,
    BaseLLM,
    LLMConfig,
    LLMResponse,
    OpenAILLM,
    create_llm,
)
from .multimodal import (
    build_multimodal_content,
    encode_image_to_base64,
)
from .types import (  # Message 类型; Function/Tool 定义; Dialog 和 Trajectory
    AssistantMessage,
    BaseMessage,
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

__all__ = [
    # Multimodal
    'encode_image_to_base64',
    'build_multimodal_content',
    # LLM
    'BaseLLM',
    'LLMConfig',
    'LLMResponse',
    'OpenAILLM',
    'AnthropicLLM',
    'create_llm',
    # Types
    'MessageRole',
    'BaseMessage',
    'SystemMessage',
    'UserMessage',
    'AssistantMessage',
    'ToolMessage',
    'Message',
    'FunctionCall',
    'ToolCall',
    'FunctionSpec',
    'ToolSpec',
    'Dialog',
    'StepRecord',
    'Trajectory',
    'TaskInstance',
]
