"""Native Anthropic Messages transport."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import anthropic

from matmaster.providers.transport import Transport
from matmaster.types.errors import LLMError
from matmaster.types.message_normalization import validate_tool_turn_sequence
from matmaster.types.messages import (
    AssistantMessage,
    ImageContentPart,
    LLMResponse,
    Message,
    StreamChunk,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


@dataclass(frozen=True)
class AnthropicPromptCacheOptions:
    """Provider-local Anthropic prompt cache controls."""

    system_prompt_breakpoint: bool
    cache_control: dict[str, str]
    automatic: bool = False
    latest_user_breakpoint: bool = True
    tool_result_breakpoint: bool = False
    flexible_breakpoint: bool = False
    max_breakpoints: int = 4
    min_flexible_chars: int = 1000


def _text_block(text: str | None) -> list[dict[str, Any]]:
    if not text:
        return []
    return [{"type": "text", "text": text}]


def _image_block(image: ImageContentPart) -> dict[str, Any]:
    url = image.url
    if url.startswith("data:") and ";base64," in url:
        header, data = url.split(";base64,", 1)
        media_type = image.mime_type or header.removeprefix("data:") or "image/png"
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }
    return {"type": "image", "source": {"type": "url", "url": url}}


def _user_content_blocks(message: UserMessage) -> list[dict[str, Any]]:
    blocks = _text_block(message.content)
    blocks.extend(_image_block(image) for image in message.images)
    return blocks


def _tool_use_blocks(tool_calls: list[ToolCallData] | None) -> list[dict[str, Any]]:
    return [
        {
            "type": "tool_use",
            "id": tc.id,
            "name": tc.name,
            "input": tc.arguments,
        }
        for tc in (tool_calls or [])
    ]


def _tool_result_block(message: ToolMessage) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": message.tool_call_id,
        "content": message.content or "",
    }


def _thinking_blocks_from_payload(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not payload:
        return []
    raw = payload.get("thinking")
    if not isinstance(raw, list):
        return []
    return [dict(block) for block in raw if isinstance(block, dict)]


def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    converted: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function", {})
        item = {
            "name": function["name"],
            "input_schema": function.get("parameters")
            or {"type": "object", "properties": {}},
        }
        if function.get("description"):
            item["description"] = function["description"]
        converted.append(item)
    return converted


def _map_tool_choice(tool_choice: str | dict | None) -> dict[str, str] | None:
    if tool_choice is None or tool_choice == "auto":
        return {"type": "auto"}
    if tool_choice == "none":
        return {"type": "none"}
    raise LLMError(
        "anthropic_messages with thinking enabled supports only tool_choice auto/none",
        retryable=False,
        error_category="bad_request",
    )


class AnthropicMessagesTransport(Transport):
    """Native Anthropic Messages API transport."""

    transport_tag = "anthropic_messages"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        prompt_cache_options: AnthropicPromptCacheOptions | None = None,
        timeout: float = 300.0,
        stream_timeout: float | None = None,
        stream_idle_timeout: float | None = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        super().__init__(
            timeout=timeout,
            stream_timeout=stream_timeout,
            stream_idle_timeout=stream_idle_timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._max_tokens = max_tokens
        self._reasoning_effort = reasoning_effort
        self._prompt_cache_options = prompt_cache_options

    async def _open_client(self) -> anthropic.AsyncAnthropic:
        import httpx

        read_t = float(max(self.stream_idle_timeout, self.stream_timeout) + 10)
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=read_t, write=30.0, pool=15.0)
        )
        return anthropic.AsyncAnthropic(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=0,
            http_client=http_client,
        )

    async def _close_client(self, client: anthropic.AsyncAnthropic) -> None:
        await client.close()

    def _assistant_to_wire(self, message: AssistantMessage) -> dict[str, Any]:
        blocks: list[dict[str, Any]] = []
        blocks.extend(_thinking_blocks_from_payload(self._claim_provider_state(message)))
        blocks.extend(_text_block(message.content))
        blocks.extend(_tool_use_blocks(message.tool_calls))
        return {"role": "assistant", "content": blocks}

    def convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        validate_tool_turn_sequence(messages)
        out: list[dict[str, Any]] = []
        idx = 0
        while idx < len(messages):
            message = messages[idx]
            if isinstance(message, SystemMessage):
                idx += 1
                continue
            if isinstance(message, UserMessage):
                out.append({"role": "user", "content": _user_content_blocks(message)})
                idx += 1
                continue
            if isinstance(message, AssistantMessage):
                out.append(self._assistant_to_wire(message))
                idx += 1
                if not message.tool_calls:
                    continue
                result_blocks: list[dict[str, Any]] = []
                while idx < len(messages) and isinstance(messages[idx], ToolMessage):
                    result_blocks.append(_tool_result_block(messages[idx]))
                    idx += 1
                if idx < len(messages) and isinstance(messages[idx], UserMessage):
                    result_blocks.extend(_user_content_blocks(messages[idx]))
                    idx += 1
                if result_blocks:
                    out.append({"role": "user", "content": result_blocks})
                continue
            if isinstance(message, ToolMessage):
                out.append({"role": "user", "content": [_tool_result_block(message)]})
                idx += 1
                continue
            idx += 1
        return out

    def build_kwargs(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        *,
        tool_choice: str | dict | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        system_messages = [m for m in messages if isinstance(m, SystemMessage)]
        system_text = "\n\n".join(m.content or "" for m in system_messages).strip()
        converted_tools = _convert_tools(tools)
        mapped_tool_choice = _map_tool_choice(tool_choice)
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": self.convert_messages(messages),
            "thinking": {"type": "adaptive", "display": "summarized"},
        }
        if system_text:
            kwargs["system"] = system_text
        if self._reasoning_effort:
            kwargs["output_config"] = {"effort": self._reasoning_effort}
        if self._max_tokens is not None:
            kwargs["max_tokens"] = self._max_tokens
        if converted_tools:
            kwargs["tools"] = converted_tools
            if mapped_tool_choice is not None:
                kwargs["tool_choice"] = mapped_tool_choice
        elif tool_choice == "none":
            kwargs["tool_choice"] = {"type": "none"}
        if stream:
            kwargs["stream"] = True
        return kwargs

    def normalize_response(self, raw: Any) -> LLMResponse:
        raise NotImplementedError

    async def normalize_stream(self, raw_iter: Any) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError
        yield StreamChunk()

    def classify_error(self, exc: Exception) -> LLMError | None:
        if isinstance(exc, LLMError):
            return None
        return None

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        raise NotImplementedError

    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError
        yield StreamChunk()
