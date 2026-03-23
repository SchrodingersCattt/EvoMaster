"""OpenAI 兼容 API（含 Azure）LLM 实现。"""

from __future__ import annotations

import os
from typing import Any, Callable

from evomaster.utils.types import AssistantMessage, Dialog, FunctionCall, ToolCall

from .base import BaseLLM
from .config_models import LLMResponse
from .helpers import (
    _azure_deployment_name,
    _build_reasoning_request_overrides,
    _extract_reasoning_delta,
    _is_azure_base_url,
    _merge_api_message_extras,
    _merge_request_overrides,
    _normalize_request_params,
)
from .sanitize import _sanitize_tool_call_arguments


class OpenAILLM(BaseLLM):
    """OpenAI LLM 实现

    支持 OpenAI API、Azure OpenAI 和兼容接口（如 vLLM, Ollama 等）。
    Azure 时使用 AzureOpenAI 客户端，自动走 /openai/deployments/<name>/chat/completions?api-version=...
    """

    def _setup(self) -> None:
        """设置 OpenAI 或 Azure OpenAI 客户端"""
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

        base_url = (self.config.base_url or '').strip().rstrip('/')
        if _is_azure_base_url(base_url):
            # Azure OpenAI：使用 AzureOpenAI 客户端，否则会 404（需 /openai/deployments/.../chat/completions?api-version=）
            try:
                from openai import AzureOpenAI
            except ImportError:
                client_kwargs: dict[str, Any] = {
                    'api_key': self.config.api_key,
                    'base_url': base_url,
                }
                if _http_client is not None:
                    client_kwargs['http_client'] = _http_client
                self.client = OpenAI(**client_kwargs)
                self._use_azure_client = False
                self.logger.warning(
                    'Azure endpoint detected but AzureOpenAI not available; using OpenAI client (may 404).'
                )
            else:
                api_version = self.config.api_version or os.environ.get(
                    'AZURE_API_VERSION', '2024-06-01'
                )
                azure_kwargs: dict[str, Any] = {
                    'api_key': self.config.api_key,
                    'azure_endpoint': (
                        base_url if '://' in base_url else f"https://{base_url}"
                    ),
                    'api_version': api_version,
                }
                if _http_client is not None:
                    azure_kwargs['http_client'] = _http_client
                self.client = AzureOpenAI(**azure_kwargs)
                self._use_azure_client = True
        else:
            client_kwargs = {'api_key': self.config.api_key}
            if base_url:
                client_kwargs['base_url'] = base_url
            if _http_client is not None:
                client_kwargs['http_client'] = _http_client
            self.client = OpenAI(**client_kwargs)
            self._use_azure_client = False

    def _call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """调用 OpenAI API 或 Azure OpenAI API"""
        model = (
            _azure_deployment_name(self.config.model)
            if getattr(self, '_use_azure_client', False)
            else self.config.model
        )
        use_azure = getattr(self, '_use_azure_client', False)
        request_params = {
            'model': model,
            'messages': messages,
            'timeout': kwargs.get('timeout', self.config.timeout),
        }
        # 部分 Azure 模型仅支持 temperature=1，不传则用 API 默认
        if not use_azure:
            request_params['temperature'] = kwargs.get(
                'temperature', self.config.temperature
            )

        if self.config.max_tokens:
            val = kwargs.get('max_tokens', self.config.max_tokens)
            # Azure 新模型要求用 max_completion_tokens，不能用 max_tokens
            if getattr(self, '_use_azure_client', False):
                request_params['max_completion_tokens'] = val
            else:
                request_params['max_tokens'] = val

        if tools:
            request_params['tools'] = tools
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

        # 解析响应（防护：API 可能返回 None 或空 choices，例如内容过滤、限流或兼容接口异常）
        if not response.choices or len(response.choices) == 0:
            err_msg = (
                'LLM API returned no choices (response.choices is None or empty). '
                'Possible causes: content filtering, rate limit, or provider-specific empty response.'
            )
            if hasattr(response, 'model') and response.model:
                err_msg += f" Model: {response.model}"
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
            },
        )

    def query_stream(
        self,
        dialog: Dialog,
        on_token: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> AssistantMessage:
        """OpenAI 流式查询实现，使用 stream=True 逐 token 回调。

        同时支持工具调用（tool_calls）：文本 delta 实时通过 on_token 推送，
        tool_call delta 按 index 累积，流结束后组装为完整 AssistantMessage。
        这避免了 dialog.tools 非空时回退到阻塞 query() 导致前端卡死的问题。
        """
        messages = dialog.get_messages_for_api()
        tools = self._convert_tools(dialog.tools) if dialog.tools else None

        model = (
            _azure_deployment_name(self.config.model)
            if getattr(self, '_use_azure_client', False)
            else self.config.model
        )
        use_azure = getattr(self, '_use_azure_client', False)
        # 流式首 token 超时：优先用 stream_timeout，否则回退到 timeout
        _st = self.config.stream_timeout
        _stream_effective_timeout = kwargs.pop(
            'timeout', _st if _st is not None else self.config.timeout
        )
        request_params: dict[str, Any] = {
            'model': model,
            'messages': messages,
            'stream': True,
            'timeout': _stream_effective_timeout,
        }
        if not use_azure:
            request_params['temperature'] = kwargs.get(
                'temperature', self.config.temperature
            )
        if self.config.max_tokens:
            val = kwargs.get('max_tokens', self.config.max_tokens)
            if use_azure:
                request_params['max_completion_tokens'] = val
            else:
                request_params['max_tokens'] = val
        if tools:
            request_params['tools'] = tools
            request_params['tool_choice'] = kwargs.get('tool_choice', 'auto')
        if 'response_format' in kwargs and kwargs['response_format'] is not None:
            request_params['response_format'] = kwargs['response_format']

        request_params = _normalize_request_params(self.config, request_params)
        request_params = _merge_request_overrides(
            request_params, _build_reasoning_request_overrides(self.config)
        )

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
                # 记录 finish_reason（最后一个非 None 的值）
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
            self.logger.warning('OpenAI stream failed, falling back to query(): %s', e)
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
