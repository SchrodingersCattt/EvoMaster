"""Message hierarchy and LLM response types for the agent kernel.

Defines the message types (SystemMessage, UserMessage, AssistantMessage,
ToolMessage) used in the kernel execution loop, plus LLMResponse and
StreamChunk for LLM provider return values. All messages produce
OpenAI-compatible dict format via to_api_dict().
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


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


def _coerce_parsed_tool_arguments(value: Any) -> dict[str, Any] | None:
    """Normalize parsed tool-call payloads to a dict when possible."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            reparsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
        return _coerce_parsed_tool_arguments(reparsed)
    return None


def _escape_literal_control_chars_in_strings(raw: str) -> str:
    """Escape raw control characters that commonly appear in multiline content."""
    if not raw:
        return raw

    repaired: list[str] = []
    in_string = False
    escape = False
    changed = False

    for ch in raw:
        if in_string:
            if escape:
                repaired.append(ch)
                escape = False
                continue
            if ch == "\\":
                repaired.append(ch)
                escape = True
                continue
            if ch == '"':
                repaired.append(ch)
                in_string = False
                continue
            if ch == "\n":
                repaired.append("\\n")
                changed = True
                continue
            if ch == "\r":
                repaired.append("\\r")
                changed = True
                continue
            if ch == "\t":
                repaired.append("\\t")
                changed = True
                continue
            repaired.append(ch)
            continue

        repaired.append(ch)
        if ch == '"':
            in_string = True

    if not changed:
        return raw
    return "".join(repaired)


def _parse_tool_arguments_json_prefix(raw: str) -> dict[str, Any] | None:
    """Parse the first complete JSON document from a payload with trailing noise."""
    text = raw.strip()
    if not text:
        return None

    try:
        parsed, end = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return None

    coerced = _coerce_parsed_tool_arguments(parsed)
    if coerced is None:
        return None

    if text[end:].strip():
        logger.warning(
            "Tool call arguments contained trailing characters; "
            "parsed the leading JSON document only."
        )
    return coerced


def parse_tool_arguments(raw: str) -> dict[str, Any]:
    """Parse JSON arguments string from streaming tool call accumulation.

    Used at the provider boundary (AgentKernel streaming, OpenAI provider)
    to convert raw JSON strings into dict arguments for ToolCallData.
    """
    if not raw:
        return {}

    candidates = [raw]
    escaped_controls = _escape_literal_control_chars_in_strings(raw)
    if escaped_controls != raw:
        candidates.append(escaped_controls)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            parsed = None
        coerced = _coerce_parsed_tool_arguments(parsed)
        if coerced is not None:
            return coerced

        prefixed = _parse_tool_arguments_json_prefix(candidate)
        if prefixed is not None:
            return prefixed

    logger.warning("Failed to parse tool call arguments: %s", raw[:200])
    return {"_raw": raw}


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
    usage: dict[str, int] = Field(
        default_factory=dict,
        description="Per-call totals for kernel accumulation (scalar ints).",
    )
    usage_vendor: dict[str, Any] | None = Field(
        default=None,
        description="Provider-native usage snapshot (may include nested structs).",
    )
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
    usage_vendor: dict[str, Any] | None = None
