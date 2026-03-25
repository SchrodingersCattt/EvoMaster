"""Anthropic Claude API LLM 实现。"""

from __future__ import annotations

import json
from typing import Any, Callable

from evomaster.utils.types import AssistantMessage, Dialog, FunctionCall, ToolCall

from .base import BaseLLM
from .config_models import LLMResponse


class AnthropicLLM(BaseLLM):
    """Anthropic LLM 实现

    支持 Claude 系列模型。
    """

    def _setup(self) -> None:
        """设置 Anthropic 客户端"""
        try:
            from anthropic import Anthropic
        except ImportError:
            raise ImportError(
                'Anthropic package not installed. Install with: pip install anthropic'
            )

        # API key 必须在配置中提供
        if not self.config.api_key:
            raise ValueError('Anthropic API key must be provided in config')

        # 创建客户端
        client_kwargs = {'api_key': self.config.api_key}
        if self.config.base_url:
            client_kwargs['base_url'] = self.config.base_url

        self.client = Anthropic(**client_kwargs)

    def _call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """调用 Anthropic API"""
        # Anthropic 需要分离 system message
        system_message = None
        user_messages = []

        for msg in messages:
            if msg['role'] == 'system':
                system_message = msg['content']
            else:
                user_messages.append(msg)

        # 构建请求参数
        request_params = {
            'model': self.config.model,
            'messages': user_messages,
            'max_tokens': kwargs.get('max_tokens', self.config.max_tokens or 4096),
            'temperature': kwargs.get('temperature', self.config.temperature),
            'timeout': kwargs.get('timeout', self.config.timeout),
        }

        if system_message:
            request_params['system'] = system_message

        if tools:
            request_params['tools'] = tools
            request_params['tool_choice'] = kwargs.get('tool_choice', {'type': 'auto'})

        # 调用 API
        response = self.client.messages.create(**request_params)

        # 解析响应
        content_text = None
        tool_calls = None

        for content in response.content:
            if content.type == 'text':
                content_text = content.text
            elif content.type == 'tool_use':
                if tool_calls is None:
                    tool_calls = []
                # Anthropic 的工具调用格式需要转换
                tool_calls.append(
                    ToolCall(
                        id=content.id,
                        type='function',
                        function=FunctionCall(
                            name=content.name,
                            arguments=json.dumps(content.input),
                        ),
                    )
                )

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            finish_reason=response.stop_reason,
            usage={
                'prompt_tokens': response.usage.input_tokens,
                'completion_tokens': response.usage.output_tokens,
                'total_tokens': response.usage.input_tokens
                + response.usage.output_tokens,
            },
            meta={
                'model': response.model,
                'response_id': response.id,
            },
        )

    def query_stream(
        self,
        dialog: Dialog,
        on_token: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> AssistantMessage:
        """Anthropic 流式查询实现，使用 client.messages.stream() 逐 token 回调。

        注意：流式模式下不支持工具调用（tool_calls），若 dialog 包含 tools，
        则回退到非流式 query()。
        """
        # 若有工具定义，回退到非流式
        if dialog.tools:
            return super().query_stream(dialog, on_token=on_token, **kwargs)

        # Anthropic 需要分离 system message
        messages = dialog.get_messages_for_api()
        system_message = None
        user_messages = []
        for msg in messages:
            if msg['role'] == 'system':
                system_message = msg['content']
            else:
                user_messages.append(msg)

        # 流式首 token 超时：优先用 stream_timeout，否则回退到 timeout
        _st = self.config.stream_timeout
        _stream_effective_timeout = kwargs.pop(
            'timeout', _st if _st is not None else self.config.timeout
        )
        request_params: dict[str, Any] = {
            'model': self.config.model,
            'messages': user_messages,
            'max_tokens': kwargs.get('max_tokens', self.config.max_tokens or 4096),
            'temperature': kwargs.get('temperature', self.config.temperature),
            'timeout': _stream_effective_timeout,
        }
        if system_message:
            request_params['system'] = system_message

        full_content: list[str] = []
        stream_model: str | None = None
        try:
            with self.client.messages.stream(**request_params) as stream_ctx:
                for text in stream_ctx.text_stream:
                    if text:
                        full_content.append(text)
                        if on_token is not None:
                            on_token(text)
                # 流结束后获取最终消息以提取 model 名
                final_msg = stream_ctx.get_final_message()
                if final_msg and hasattr(final_msg, 'model'):
                    stream_model = final_msg.model
        except Exception as e:
            self.logger.warning(
                'Anthropic stream failed, falling back to query(): %s', e
            )
            return super().query_stream(dialog, on_token=on_token, **kwargs)

        return AssistantMessage(
            content=''.join(full_content),
            meta={'model': stream_model or self.config.model},
        )
