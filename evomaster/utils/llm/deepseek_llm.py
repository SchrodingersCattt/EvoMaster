"""DeepSeek LLM 实现（OpenAI 兼容）。"""

from __future__ import annotations

from typing import Any, Callable

from evomaster.utils.types import AssistantMessage, Dialog, FunctionCall, ToolCall

from .base import BaseLLM
from .config_models import LLMResponse
from .helpers import (
    _build_reasoning_request_overrides,
    _extract_reasoning_delta,
    _merge_api_message_extras,
    _merge_request_overrides,
    _normalize_request_params,
)
from .sanitize import _sanitize_tool_call_arguments


class DeepSeekLLM(BaseLLM):
    """DeepSeek LLM 实现

    支持 Chat Completion API 和 Completion API。
    """

    def _setup(self) -> None:
        """设置 OpenAI 客户端（兼容 DeepSeek API）"""
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                'OpenAI package not installed. Install with: pip install openai'
            )

        # API key 必须在配置中提供
        if not self.config.api_key:
            raise ValueError('OpenAI API key must be provided in config')

        # 构造 httpx 客户端：
        # - connect timeout：固定 15s
        # - read timeout：使用 stream_idle_timeout（流式 chunk 间隔保护）
        #   必须大于 openai SDK 层的 stream_timeout，避免 httpx 比 SDK 更早触发 ReadTimeout。
        #   取 max(stream_idle_timeout, stream_timeout_or_fallback) + 10s 作为安全边距。
        # - write/pool timeout：固定值
        try:
            import httpx as _httpx

            _first_token_t = (
                self.config.stream_timeout
                if self.config.stream_timeout is not None
                else self.config.timeout
            )
            _read_t = float(max(self.config.stream_idle_timeout, _first_token_t) + 10)
            _http_client = _httpx.Client(
                timeout=_httpx.Timeout(
                    connect=15.0,
                    read=_read_t,
                    write=30.0,
                    pool=15.0,
                )
            )
        except ImportError:
            _http_client = None

        # 创建客户端
        client_kwargs: dict[str, Any] = {'api_key': self.config.api_key}
        if self.config.base_url:
            client_kwargs['base_url'] = self.config.base_url
        if _http_client is not None:
            client_kwargs['http_client'] = _http_client

        self.client = OpenAI(**client_kwargs)

    def _messages_to_prompt(self, messages: list[dict[str, Any]]) -> str:
        """将消息列表转换为单个 prompt 字符串（用于 Completion API）

        格式与 X-Master 的 r1_tool.jinja 模板一致
        """
        parts = []
        for msg in messages:
            role = msg.get('role', '')
            content = msg.get('content', '')

            if role == 'system':
                parts.append(content)
            elif role == 'user':
                # 与 X-Master r1_tool.jinja / 上游 DeepSeek 模板一致：<|User|> / <|Assistant|>（全角竖线 U+FF5C）
                parts.append(
                    f"\u003c\uff5cUser\uff5c> {content} \u003c\uff5cAssistant\uff5c>"
                )
            elif role == 'assistant':
                parts.append(content)
            elif role == 'tool':
                # 工具结果包装在 execution_results 标签中
                parts.append(f"<execution_results>{content}</execution_results>")

        return ''.join(parts)

    def _call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """调用 DeepSeek API"""
        if self.config.use_completion_api:
            return self._call_completion(messages, **kwargs)
        else:
            return self._call_chat(messages, tools, **kwargs)

    def _call_completion(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> LLMResponse:
        """调用 Completion API"""
        prompt = self._messages_to_prompt(messages)

        request_params = {
            'model': self.config.model,
            'prompt': prompt,
            'temperature': kwargs.get('temperature', self.config.temperature),
            'timeout': kwargs.get('timeout', self.config.timeout),
        }

        if self.config.max_tokens:
            request_params['max_tokens'] = kwargs.get(
                'max_tokens', self.config.max_tokens
            )

        # 调用 Completion API
        response = self.client.completions.create(**request_params)

        # 解析响应（防护：API 可能返回 None 或空 choices）
        if not response.choices or len(response.choices) == 0:
            err_msg = 'LLM API returned no choices (response.choices is None or empty).'
            self.logger.warning(err_msg)
            raise ValueError(err_msg)

        choice = response.choices[0]

        return LLMResponse(
            content=choice.text,
            tool_calls=None,  # Completion API 不支持原生 tool calls
            finish_reason=choice.finish_reason,
            usage={
                'prompt_tokens': response.usage.prompt_tokens,
                'completion_tokens': response.usage.completion_tokens,
                'total_tokens': response.usage.total_tokens,
            },
            meta={
                'model': response.model,
                'response_id': response.id,
                'api_type': 'completion',
            },
        )

    def _call_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """调用 Chat Completion API"""
        # 构建请求参数
        request_params = {
            'model': self.config.model,
            'messages': messages,
            'temperature': kwargs.get('temperature', self.config.temperature),
            'timeout': kwargs.get('timeout', self.config.timeout),
            'extra_body': {
                'chat_template_kwargs': {'thinking': True},
                'separate_reasoning': True,
            },
        }

        if self.config.max_tokens:
            request_params['max_tokens'] = kwargs.get(
                'max_tokens', self.config.max_tokens
            )

        if tools:
            # 清理 tools 中的 None 值（如 strict=None），某些 API 不接受 None
            cleaned_tools = []
            for tool in tools:
                cleaned_tool = tool.copy()
                if 'function' in cleaned_tool and isinstance(
                    cleaned_tool['function'], dict
                ):
                    cleaned_function = cleaned_tool['function'].copy()
                    # 移除 strict=None 字段
                    if cleaned_function.get('strict') is None:
                        cleaned_function.pop('strict', None)
                    cleaned_tool['function'] = cleaned_function
                cleaned_tools.append(cleaned_tool)
            request_params['tools'] = cleaned_tools
            request_params['tool_choice'] = kwargs.get('tool_choice', 'auto')

        # 支持 response_format（如 {"type": "json_object"} 或 JSON Schema）
        if 'response_format' in kwargs and kwargs['response_format'] is not None:
            request_params['response_format'] = kwargs['response_format']

        request_params = _normalize_request_params(self.config, request_params)
        request_params = _merge_request_overrides(
            request_params, _build_reasoning_request_overrides(self.config)
        )

        # 调用 API
        response = self.client.chat.completions.create(**request_params)

        # 解析响应（防护：API 可能返回 None 或空 choices）
        if not response.choices or len(response.choices) == 0:
            err_msg = 'LLM API returned no choices (response.choices is None or empty).'
            self.logger.warning(err_msg)
            raise ValueError(err_msg)

        choice = response.choices[0]
        message = choice.message

        # 提取工具调用（sanitize arguments 防止非法 JSON 污染对话历史）
        tool_calls = None
        if message.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    type='function',
                    function=FunctionCall(
                        name=tc.function.name,
                        arguments=_sanitize_tool_call_arguments(tc.function.arguments),
                    ),
                )
                for tc in message.tool_calls
            ]

        return LLMResponse(
            content=message.content,
            reasoning_content=getattr(message, 'reasoning_content', None),
            api_message_extras=getattr(message, 'model_extra', {}) or {},
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            usage={
                'prompt_tokens': response.usage.prompt_tokens,
                'completion_tokens': response.usage.completion_tokens,
                'total_tokens': response.usage.total_tokens,
            },
            meta={
                'model': response.model,
                'response_id': response.id,
                'api_type': 'chat',
            },
        )

    def query_stream(
        self,
        dialog: Dialog,
        on_token: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> AssistantMessage:
        """DeepSeek 流式查询实现（兼容 OpenAI SDK，使用 stream=True）。

        同时支持工具调用（tool_calls）：文本 delta 实时通过 on_token 推送，
        tool_call delta 按 index 累积，流结束后组装为完整 AssistantMessage。
        Completion API 模式不支持流式，仍回退到非流式 query()。
        """
        # Completion API 不支持流式
        if self.config.use_completion_api:
            return super().query_stream(dialog, on_token=on_token, **kwargs)

        messages = dialog.get_messages_for_api()
        tools = self._convert_tools(dialog.tools) if dialog.tools else None

        # 流式首 token 超时：优先用 stream_timeout，否则回退到 timeout
        _st = self.config.stream_timeout
        _stream_effective_timeout = kwargs.pop(
            'timeout', _st if _st is not None else self.config.timeout
        )
        request_params: dict[str, Any] = {
            'model': self.config.model,
            'messages': messages,
            'temperature': kwargs.get('temperature', self.config.temperature),
            'stream': True,
            'timeout': _stream_effective_timeout,
        }
        if self.config.max_tokens:
            request_params['max_tokens'] = kwargs.get(
                'max_tokens', self.config.max_tokens
            )
        if tools:
            # 清理 tools 中的 None 值（如 strict=None），某些 API 不接受 None
            cleaned_tools = []
            for tool in tools:
                cleaned_tool = tool.copy()
                if 'function' in cleaned_tool and isinstance(
                    cleaned_tool['function'], dict
                ):
                    cleaned_function = cleaned_tool['function'].copy()
                    if cleaned_function.get('strict') is None:
                        cleaned_function.pop('strict', None)
                    cleaned_tool['function'] = cleaned_function
                cleaned_tools.append(cleaned_tool)
            request_params['tools'] = cleaned_tools
            request_params['tool_choice'] = kwargs.get('tool_choice', 'auto')

        full_content: list[str] = []
        full_reasoning_content: list[str] = []
        assistant_extra_acc: dict[str, Any] = {}
        # tool_call delta 按 index 累积：{index: {"id": str, "name": str, "arguments": str}}
        tool_calls_acc: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        stream_model: str | None = None  # 从第一个 chunk 捕获实际模型名

        try:
            stream = self.client.chat.completions.create(**request_params)
            for chunk in stream:
                # 从首个 chunk 捕获实际模型名（供 trajectory 记录）
                if stream_model is None and hasattr(chunk, 'model') and chunk.model:
                    stream_model = chunk.model
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta is None:
                    continue
                # 记录 finish_reason
                if choice.finish_reason is not None:
                    finish_reason = choice.finish_reason
                # 文本 token → 实时推送
                if delta.content:
                    full_content.append(delta.content)
                reasoning_delta = _extract_reasoning_delta(delta)
                if reasoning_delta:
                    full_reasoning_content.append(reasoning_delta)
                    if on_token is not None:
                        on_token(reasoning_delta)
                assistant_extra_acc = _merge_api_message_extras(
                    assistant_extra_acc,
                    getattr(delta, 'model_extra', {}) or {},
                )
                # tool_call delta → 按 index 累积
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                'id': '',
                                'name': '',
                                'arguments': '',
                            }
                        if tc_delta.id:
                            tool_calls_acc[idx]['id'] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tool_calls_acc[idx]['name'] += tc_delta.function.name
                            if tc_delta.function.arguments:
                                tool_calls_acc[idx][
                                    'arguments'
                                ] += tc_delta.function.arguments
        except Exception as e:
            self.logger.warning(
                'DeepSeek stream failed, falling back to query(): %s', e
            )
            return super().query_stream(dialog, on_token=on_token, **kwargs)

        # 组装 tool_calls（按 index 排序保证顺序；sanitize arguments 防止非法 JSON 污染对话历史）
        tool_calls: list[ToolCall] | None = None
        if tool_calls_acc:
            tool_calls = [
                ToolCall(
                    id=v['id'],
                    type='function',
                    function=FunctionCall(
                        name=v['name'],
                        arguments=_sanitize_tool_call_arguments(v['arguments']),
                    ),
                )
                for _, v in sorted(tool_calls_acc.items())
            ]

        content = ''.join(full_content) or None
        return AssistantMessage(
            content=content,
            tool_calls=tool_calls,
            meta={
                'model': stream_model,
                'finish_reason': finish_reason,
                'reasoning_content': ''.join(full_reasoning_content) or None,
                'api_message_extras': assistant_extra_acc,
            },
        )
