"""Native OpenAI Responses transport."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from matmaster.providers.transport import (
    dump_model_to_jsonable,
    tool_image_relay_label,
)
from matmaster.providers.transports.openai_common import OpenAISDKTransport
from matmaster.types.errors import LLMError
from matmaster.types.message_normalization import validate_tool_turn_sequence
from matmaster.types.messages import (
    AssistantMessage,
    ImageContentPart,
    LLMResponse,
    Message,
    ProviderState,
    StreamChunk,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
    parse_tool_arguments,
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


def _relay_content_for(message: ToolMessage) -> list[dict[str, Any]]:
    if not message.images:
        return []
    content: list[dict[str, Any]] = [
        {"type": "input_text", "text": tool_image_relay_label(message)}
    ]
    content.extend(_input_image_part(image) for image in message.images)
    return content


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


def _reasoning_items_from_output(output: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in output or []:
        if getattr(item, "type", None) == "reasoning":
            dumped = dump_model_to_jsonable(item)
            if isinstance(dumped, dict):
                items.append(dumped)
    return items


def _provider_state_from_reasoning(
    items: list[dict[str, Any]],
) -> ProviderState | None:
    if not items:
        return None
    return ProviderState(transport="responses", payload={"reasoning": items})


def _responses_usage_to_scalar_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    prompt = int(getattr(usage, "input_tokens", 0) or 0)
    completion = int(getattr(usage, "output_tokens", 0) or 0)
    total = getattr(usage, "total_tokens", None)
    out = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": int(total) if isinstance(total, int) else prompt + completion,
    }
    input_details = getattr(usage, "input_tokens_details", None)
    cached = getattr(input_details, "cached_tokens", None) if input_details else None
    if isinstance(cached, int) and cached > 0:
        out["cache_read_tokens"] = cached
    output_details = getattr(usage, "output_tokens_details", None)
    reasoning = (
        getattr(output_details, "reasoning_tokens", None) if output_details else None
    )
    if isinstance(reasoning, int) and reasoning > 0:
        out["reasoning_tokens"] = reasoning
    return out


def _finish_reason_from_response(response: Any) -> str | None:
    output = getattr(response, "output", None) or []
    has_function_call = any(
        getattr(item, "type", None) == "function_call" for item in output
    )
    has_refusal = any(
        getattr(part, "type", None) == "refusal"
        for item in output
        if getattr(item, "type", None) == "message"
        for part in (getattr(item, "content", None) or [])
    )
    if has_function_call:
        return "tool_calls"
    if has_refusal:
        return "content_filter"
    if getattr(response, "status", None) == "incomplete":
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None) if details is not None else None
        if reason == "max_output_tokens":
            return "length"
        return reason or "stop"
    return "stop"


def _is_non_retryable_responses_bad_request(text: str) -> bool:
    lowered = text.lower()
    patterns = (
        "reasoning",
        "encrypted",
        "without its required following item",
        "previous_response_id",
        "store",
        "function_call",
    )
    return any(pattern in lowered for pattern in patterns)


def _llm_error_from_failed_response(response: Any) -> LLMError:
    error = getattr(response, "error", None)
    code = str(getattr(error, "code", "") or "").lower()
    message = getattr(error, "message", None) or "responses stream failed"
    text = f"{code} {message}".lower()
    if "context" in text and ("length" in text or "token" in text):
        return LLMError(message, retryable=False, error_category="context_overflow")
    if "rate" in code and "limit" in code:
        return LLMError(message, retryable=True, error_category="rate_limit")
    if code in ("authentication_error", "permission_denied", "auth_error"):
        return LLMError(message, retryable=False, error_category="auth")
    bad_request_like = code in ("bad_request", "invalid_request_error")
    non_retryable_bad_request = _is_non_retryable_responses_bad_request(text)
    if bad_request_like or non_retryable_bad_request:
        return LLMError(
            message,
            retryable=not non_retryable_bad_request,
            error_category="bad_request",
        )
    return LLMError(message, retryable=True, error_category="server")


class ResponsesTransport(OpenAISDKTransport):
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
        pending_relay: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, SystemMessage):
                continue
            if isinstance(message, ToolMessage):
                out.append(_function_call_output_item(message))
                pending_relay.extend(_relay_content_for(message))
                continue
            if pending_relay and isinstance(message, UserMessage):
                user_item = _user_input_item(message)
                user_item["content"] = pending_relay + user_item["content"]
                out.append(user_item)
                pending_relay = []
                continue
            if pending_relay:
                out.append({"role": "user", "content": pending_relay})
                pending_relay = []
            if isinstance(message, UserMessage):
                out.append(_user_input_item(message))
                continue
            if isinstance(message, AssistantMessage):
                out.extend(self._assistant_to_items(message))
                continue
        if pending_relay:
            out.append({"role": "user", "content": pending_relay})
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
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[ToolCallData] = []
        for item in getattr(raw, "output", None) or []:
            item_type = getattr(item, "type", None)
            if item_type == "reasoning":
                for part in getattr(item, "summary", None) or []:
                    text = getattr(part, "text", "") or ""
                    if text:
                        reasoning_parts.append(text)
            elif item_type == "message":
                for part in getattr(item, "content", None) or []:
                    if getattr(part, "type", None) == "output_text":
                        text_parts.append(getattr(part, "text", "") or "")
            elif item_type == "function_call":
                tool_calls.append(
                    ToolCallData(
                        id=item.call_id,
                        name=item.name,
                        arguments=parse_tool_arguments(
                            getattr(item, "arguments", "") or ""
                        ),
                    )
                )
        reasoning_items = _reasoning_items_from_output(getattr(raw, "output", None))
        usage = getattr(raw, "usage", None)
        return LLMResponse(
            content="".join(text_parts) or None,
            reasoning_content="".join(reasoning_parts) or None,
            tool_calls=tool_calls or None,
            finish_reason=_finish_reason_from_response(raw),
            usage=_responses_usage_to_scalar_dict(usage),
            usage_vendor=dump_model_to_jsonable(usage) if usage is not None else None,
            provider_state=_provider_state_from_reasoning(reasoning_items),
        )

    async def normalize_stream(self, raw_iter: Any) -> AsyncIterator[StreamChunk]:
        item_logical_index: dict[str, int] = {}
        next_index = 0
        buffered_reasoning: list[dict[str, Any]] = []

        async for event in raw_iter:
            event_type = getattr(event, "type", None)
            if event_type == "response.output_text.delta":
                yield StreamChunk(content=getattr(event, "delta", "") or "")
                continue
            if event_type in (
                "response.reasoning_summary_text.delta",
                "response.reasoning_text.delta",
            ):
                yield StreamChunk(reasoning_content=getattr(event, "delta", "") or "")
                continue
            if event_type == "response.refusal.delta":
                yield StreamChunk(content=getattr(event, "delta", "") or "")
                continue
            if event_type == "response.output_item.added":
                item = getattr(event, "item", None)
                if getattr(item, "type", None) == "function_call":
                    item_id = getattr(item, "id", None)
                    if item_id not in item_logical_index:
                        item_logical_index[item_id] = next_index
                        next_index += 1
                    yield StreamChunk(
                        tool_call_deltas=[
                            {
                                "index": item_logical_index[item_id],
                                "id": getattr(item, "call_id", None),
                                "name": getattr(item, "name", None),
                            }
                        ]
                    )
                continue
            if event_type == "response.function_call_arguments.delta":
                item_id = getattr(event, "item_id", None)
                if item_id not in item_logical_index:
                    item_logical_index[item_id] = next_index
                    next_index += 1
                yield StreamChunk(
                    tool_call_deltas=[
                        {
                            "index": item_logical_index[item_id],
                            "arguments": getattr(event, "delta", "") or "",
                        }
                    ]
                )
                continue
            if event_type == "response.output_item.done":
                item = getattr(event, "item", None)
                if getattr(item, "type", None) == "reasoning":
                    dumped = dump_model_to_jsonable(item)
                    if isinstance(dumped, dict):
                        buffered_reasoning.append(dumped)
                continue
            if event_type in ("response.completed", "response.incomplete"):
                response = getattr(event, "response", None)
                reasoning_items = (
                    _reasoning_items_from_output(getattr(response, "output", None))
                    or buffered_reasoning
                )
                provider_state = _provider_state_from_reasoning(reasoning_items)
                if provider_state is not None:
                    yield StreamChunk(provider_state=provider_state)
                finish_reason = _finish_reason_from_response(response)
                if finish_reason:
                    yield StreamChunk(finish_reason=finish_reason)
                usage = getattr(response, "usage", None)
                if usage is not None:
                    yield StreamChunk(
                        usage=_responses_usage_to_scalar_dict(usage),
                        usage_vendor=dump_model_to_jsonable(usage),
                    )
                continue
            if event_type == "response.failed":
                response = getattr(event, "response", None)
                raise _llm_error_from_failed_response(response)

    def _is_non_retryable_bad_request(self, err_str: str) -> bool:
        return _is_non_retryable_responses_bad_request(err_str)

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        client = self._ensure_client()
        kwargs = self.build_kwargs(messages, tools, tool_choice=tool_choice)
        async with client.responses.stream(**kwargs) as stream:
            final = await stream.get_final_response()
        return self.normalize_response(final)

    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        client = self._ensure_client()
        kwargs = self.build_kwargs(messages, tools)
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            async with client.responses.stream(**kwargs) as stream:
                async for chunk in self.normalize_stream(stream):
                    yield chunk
        except Exception as exc:  # noqa: BLE001
            err = self.classify_error(exc)
            if err is not None:
                raise err from exc
            raise
