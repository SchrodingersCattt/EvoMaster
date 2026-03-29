"""Message hierarchy and LLM response types for the agent kernel.

Defines the message types (SystemMessage, UserMessage, AssistantMessage,
ToolMessage) used in the kernel execution loop, plus LLMResponse and
StreamChunk for LLM provider return values. All messages produce
OpenAI-compatible dict format via to_api_dict().
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Role(str, Enum):
    """Message role in the conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCallData(BaseModel):
    """A single tool call requested by the LLM.

    arguments is dict[str, Any], not raw JSON string -- parsing is done
    at the provider boundary.
    """

    id: str
    name: str
    arguments: dict[str, Any]


class Message(BaseModel):
    """Base message in the conversation history.

    Subclasses set role defaults. to_api_dict() produces OpenAI-compatible
    dict format for sending to the LLM API.
    """

    role: Role
    content: str | None = None

    def to_api_dict(self) -> dict[str, Any]:
        """Convert to OpenAI API-compatible dict."""
        return {"role": self.role.value, "content": self.content}


class SystemMessage(Message):
    """System instruction message."""

    role: Role = Role.SYSTEM


class UserMessage(Message):
    """User input message."""

    role: Role = Role.USER


class AssistantMessage(Message):
    """Assistant (LLM) response message.

    May include tool_calls when the LLM requests tool invocations.
    to_api_dict() includes tool_calls only when present (not None).
    """

    role: Role = Role.ASSISTANT
    tool_calls: list[ToolCallData] | None = None
    reasoning_content: str | None = None

    def to_api_dict(self) -> dict[str, Any]:
        """Convert to OpenAI API-compatible dict.

        Includes tool_calls only when self.tool_calls is not None.
        Each tool call formatted as:
        {"id": ..., "type": "function", "function": {"name": ..., "arguments": json_str}}
        """
        d: dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.tool_calls is not None:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in self.tool_calls
            ]
        return d


class ToolMessage(Message):
    """Tool execution result message.

    to_api_dict() includes tool_call_id but omits tool_name
    (OpenAI API does not use it).
    """

    role: Role = Role.TOOL
    tool_call_id: str
    tool_name: str

    def to_api_dict(self) -> dict[str, Any]:
        """Convert to OpenAI API-compatible dict."""
        return {
            "role": self.role.value,
            "content": self.content,
            "tool_call_id": self.tool_call_id,
        }


class LLMResponse(BaseModel):
    """Non-streaming LLM response from LLMProvider.chat()."""

    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCallData] | None = None
    finish_reason: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    degraded: bool = False


class StreamChunk(BaseModel):
    """Single chunk from LLMProvider.chat_stream().

    Carries incremental content, reasoning, tool call deltas, or
    stream lifecycle signals.
    """

    content: str | None = None
    reasoning_content: str | None = None
    tool_call_deltas: list[dict[str, Any]] | None = None
    finish_reason: str | None = None
    stream_state: str | None = None
    stream_id: str | None = None
    usage: dict[str, int] | None = None
