"""matmaster.engine -- Agent execution kernel.

Provides the core execution loop, message types, LLM provider protocol,
hook system, and guard pipeline for agent tool-call orchestration.
"""

from .agent import AgentKernel
from .guard_pipeline import GuardPipeline, LoopDetectionGuard
from .hooks import BaseHook, EventEmitterHook, Hook, HookAction
from .types import (
    AssistantMessage,
    LLMResponse,
    Message,
    Role,
    StreamChunk,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)

__all__ = [
    "AgentKernel",
    "AssistantMessage",
    "BaseHook",
    "EventEmitterHook",
    "GuardPipeline",
    "Hook",
    "HookAction",
    "LLMResponse",
    "LoopDetectionGuard",
    "Message",
    "Role",
    "StreamChunk",
    "SystemMessage",
    "ToolCallData",
    "ToolMessage",
    "UserMessage",
]
