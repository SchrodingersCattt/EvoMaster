"""Native OpenAI Responses transport."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import openai

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


def _input_image_part(image: ImageContentPart) -> dict[str, Any]:
    return {
        "type": "input_image",
        "image_url": image.url,
        "detail": image.detail or "auto",
    }


def _user_input_item(message: UserMessage) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if message.content:
        content.append({"type": "input_text", "text": message.content})
    content.extend(_input_image_part(image) for image in message.images)
    return {"role": "user", "content": content}


def _function_call_items(tool_calls: list[ToolCallData] | None) -> list[dict[str, Any]]:
    return [
        {
            "type": "function_call",
            "call_id": tc.id,
            "name": tc.name,
            "arguments": tc.arguments_json,
        }
        for tc in (tool_calls or [])
    ]


def _function_call_output_item(message: ToolMessage) -> dict[str, Any]:
    return {
        "type": "function_call_output",
        "call_id": message.tool_call_id,
        "output": message.content or "",
    }


def _reasoning_items_from_payload(
    payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not payload:
        return []
    raw = payload.get("reasoning")
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    converted: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function", {})
        item: dict[str, Any] = {
            "type": "function",
            "name": function["name"],
            "parameters": function.get("parameters")
            or {"type": "object", "properties": {}},
            "strict": False,
        }
        if function.get("description"):
            item["description"] = function["description"]
        converted.append(item)
    return converted


def _map_tool_choice(tool_choice: str | dict | None) -> str | dict:
    if tool_choice is None or tool_choice == "auto":
        return "auto"
    if tool_choice == "none":
        return "none"
    if tool_choice in ("required", "any"):
        return "required"
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        name = tool_choice.get("function", {}).get("name") or tool_choice.get("name")
        return {"type": "function", "name": name}
    return "auto"


class ResponsesTransport(Transport):
    """Native OpenAI Responses API transport (stateless encrypted reasoning replay)."""

    transport_tag = "responses"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        reasoning_summary: str | None = None,
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
        self._reasoning_summary = reasoning_summary

    async def _open_client(self) -> openai.AsyncOpenAI:
        import httpx

        read_t = float(max(self.stream_idle_timeout, self.stream_timeout) + 10)
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=read_t, write=30.0, pool=15.0)
        )
        return openai.AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=0,
            http_client=http_client,
        )

    async def _close_client(self, client: openai.AsyncOpenAI) -> None:
        await client.close()

    def _assistant_to_items(self, message: AssistantMessage) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        # 空回合（无 content 且无 tool_calls）丢弃 reasoning，避免孤儿 400。
        if message.content or message.tool_calls:
            items.extend(
                _reasoning_items_from_payload(self._claim_provider_state(message))
            )
        if message.content:
            items.append({"role": "assistant", "content": message.content})
        items.extend(_function_call_items(message.tool_calls))
        return items

    def convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        validate_tool_turn_sequence(messages)
        out: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, SystemMessage):
                continue
            if isinstance(message, UserMessage):
                out.append(_user_input_item(message))
                continue
            if isinstance(message, AssistantMessage):
                out.extend(self._assistant_to_items(message))
                continue
            if isinstance(message, ToolMessage):
                out.append(_function_call_output_item(message))
                continue
        return out

    def build_kwargs(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        *,
        tool_choice: str | dict | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        # Responses stream API 无 stream 形参；chat 与 chat_stream 都走
        # client.responses.stream()。
        system_messages = [m for m in messages if isinstance(m, SystemMessage)]
        instructions = "\n\n".join(m.content or "" for m in system_messages).strip()
        kwargs: dict[str, Any] = {
            "model": self._model,
            "input": self.convert_messages(messages),
            "include": ["reasoning.encrypted_content"],
            "store": False,
        }
        if instructions:
            kwargs["instructions"] = instructions
        reasoning: dict[str, str] = {}
        if self._reasoning_effort:
            reasoning["effort"] = self._reasoning_effort
        if self._reasoning_summary:
            reasoning["summary"] = self._reasoning_summary
        if reasoning:
            kwargs["reasoning"] = reasoning
        if self._max_tokens is not None:
            kwargs["max_output_tokens"] = self._max_tokens
        converted_tools = _convert_tools(tools)
        if converted_tools:
            kwargs["tools"] = converted_tools
            kwargs["tool_choice"] = _map_tool_choice(tool_choice)
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
