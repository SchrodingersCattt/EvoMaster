"""matmaster.kernel -- Agent execution kernel.

Provides the core execution loop, message types, LLM provider protocol,
hook system, and guard pipeline for agent tool-call orchestration.
"""

from .guard_pipeline import GuardPipeline, LoopDetectionGuard
from .hooks import BaseHook, EventEmitterHook, Hook, HookAction
from .kernel import AgentKernel
from .llm_provider import LLMProvider
from .openai_provider import OpenAIProvider
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
    "LLMProvider",
    "LLMResponse",
    "LoopDetectionGuard",
    "Message",
    "OpenAIProvider",
    "Role",
    "StreamChunk",
    "SystemMessage",
    "ToolCallData",
    "ToolMessage",
    "UserMessage",
]
