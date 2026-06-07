"""Message hierarchy and LLM response types for the agent kernel.

Defines the message types (SystemMessage, UserMessage, AssistantMessage,
ToolMessage) used in the kernel execution loop, plus LLMResponse and
StreamChunk for LLM provider return values.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from enum import Enum
from functools import cached_property
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

_JSON_DECODER = json.JSONDecoder()


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

    Immutability contract:
    - frozen=True blocks field rebinding (tc.arguments = ...).
    - Nested mutation of arguments (tc.arguments["k"] = v) is not blocked by
      frozen and is forbidden by convention; it would stale arguments_json.
    - Do not use model_copy(update={"arguments": ...}), because it would carry
      the stale cached arguments_json. Construct a fresh ToolCallData instead.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: dict[str, Any]

    @cached_property
    def arguments_json(self) -> str:
        """JSON-serialized arguments, cached once per instance."""
        return json.dumps(self.arguments)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> ToolCallData:
        """Copy the model, forbidding arguments replacement.

        Pydantic preserves cached_property state during model_copy. Replacing
        arguments through update after arguments_json is cached would silently
        carry stale JSON, so callers must construct a fresh instance.
        """
        if update is not None and "arguments" in update:
            raise ValueError(
                "Changing arguments via model_copy(update=...) would stale "
                "arguments_json; construct a fresh ToolCallData instead."
            )
        return super().model_copy(update=update, deep=deep)


def _coerce_parsed_tool_arguments(value: Any) -> dict[str, Any] | None:
    """Normalize parsed tool-call payloads to a dict when possible."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            reparsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return _coerce_parsed_tool_arguments(reparsed)
    return None


def _escape_literal_control_chars_in_strings(raw: str) -> str:
    """Escape raw control characters that commonly appear in multiline content."""
    if not raw:
        return raw

    # Fast path: no literal control chars anywhere → nothing to repair.
    if "\n" not in raw and "\r" not in raw and "\t" not in raw:
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
        parsed, end = _JSON_DECODER.raw_decode(text)
    except json.JSONDecodeError:
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
        except json.JSONDecodeError:
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

    Subclasses set role defaults. Wire serialization lives in transports.
    """

    role: Role
    content: str | None = None


class ImageContentPart(BaseModel):
    url: str
    mime_type: str | None = None
    detail: Literal["low", "high", "auto"] | None = None


class SystemMessage(Message):
    """System instruction message."""

    role: Role = Role.SYSTEM


class UserMessage(Message):
    """User input message."""

    role: Role = Role.USER
    images: list[ImageContentPart] = Field(default_factory=list)


class AssistantMessage(Message):
    """Assistant (LLM) response message.

    May include tool_calls when the LLM requests tool invocations.
    """

    role: Role = Role.ASSISTANT
    tool_calls: list[ToolCallData] | None = None
    reasoning_content: str | None = None
    provider_state: ProviderState | None = None

class ToolMessage(Message):
    """Tool execution result message."""

    role: Role = Role.TOOL
    tool_call_id: str
    tool_name: str


class ProviderState(BaseModel):
    """Provider 回放状态：对 kernel 不透明、transport 私有、带 transport tag。

    kernel 原样存取、不解读 payload；只有 tag 匹配的 transport 在 convert 时认领。
    payload 必须只含 JSON-serializable 值（dict/list/str/int/float/bool/None）；
    持久化统一走 model_dump(mode="json")，非 JSON 值会在持久化层炸。
    """

    model_config = ConfigDict(frozen=True)

    transport: str
    payload: dict[str, Any]


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
    provider_state: ProviderState | None = None


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
    provider_state: ProviderState | None = None
