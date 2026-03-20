"""EvoMaster LLM 接口封装

提供统一的 LLM 调用接口，支持多种提供商。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from evomaster.utils.types import AssistantMessage, Dialog, FunctionCall, ToolCall


def truncate_content(
    content: str,
    max_length: int = 5000,
    head_length: int = 2500,
    tail_length: int = 2500,
) -> str:
    """截断内容，如果超过最大长度，保留开头和结尾部分

    Args:
        content: 要截断的内容
        max_length: 最大长度阈值，超过此长度才截断
        head_length: 保留的开头部分长度
        tail_length: 保留的结尾部分长度

    Returns:
        截断后的内容
    """
    if len(content) <= max_length:
        return content
    return content[:head_length] + '\n... [truncated] ...\n' + content[-tail_length:]


_sanitize_logger = logging.getLogger(__name__)


def _sanitize_tool_call_arguments(arguments: str | None) -> str:
    """验证并修复 LLM 返回的工具调用 arguments JSON 字符串。

    LLM 偶尔会生成非法 JSON（如混入 XML 属性语法 ``is_input="true"``），
    导致下一轮 LLM 调用时 litellm/Bedrock 适配器解析失败并抛出 500 错误。
    本函数在 LLM 响应解析阶段对 arguments 做防御性清洗：

    1. 若 arguments 已是合法 JSON，原样返回。
    2. 若非法，尝试移除 JSON key 与冒号之间的 XML 属性风格 token
       （如 ``"command" is_input="true":`` → ``"command":``）后重新解析。
    3. 若修复后仍非法，返回 ``"{}"`` 并记录 ERROR 日志。
       工具层会因参数校验失败返回错误 observation，Agent 可从中恢复，
       远比让整个下一轮 LLM 调用崩溃要安全。

    Args:
        arguments: LLM 返回的 function.arguments 字符串（可为 None）。

    Returns:
        合法的 JSON 字符串。
    """
    if not arguments:
        return '{}'

    # 快速路径：合法 JSON 直接返回
    try:
        json.loads(arguments)
        return arguments
    except (json.JSONDecodeError, ValueError):
        pass

    # 修复：移除 JSON key 与冒号之间的 XML 属性风格 token
    # 例：`"command" is_input="true":` → `"command":`
    repaired = re.sub(r'(?<=")\s+\w+="[^"]*"(?=\s*:)', '', arguments)
    try:
        json.loads(repaired)
        _sanitize_logger.warning(
            '_sanitize_tool_call_arguments: repaired malformed tool call arguments. '
            'Original: %r  Repaired: %r',
            arguments,
            repaired,
        )
        return repaired
    except (json.JSONDecodeError, ValueError):
        pass

    # 无法修复：返回空对象，记录 ERROR
    _sanitize_logger.error(
        '_sanitize_tool_call_arguments: could not repair malformed tool call arguments, '
        "falling back to '{}'. Original: %r",
        arguments,
    )
    return '{}'


class LLMConfig(BaseModel):
    """LLM 配置"""

    provider: Literal['openai', 'anthropic', 'deepseek', 'openrouter'] = Field(
        description='LLM 提供商'
    )
    model: str = Field(
        description='模型名称（OpenAI 为 model id；Azure 为部署名，可写 azure/部署名）'
    )
    api_key: str = Field(description='API Key，必须在配置中提供')
    base_url: str | None = Field(default=None, description='API Base URL')
    api_version: str | None = Field(
        default=None, description='Azure 专用：API 版本，如 2024-06-01'
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description='采样温度')
    max_tokens: int | None = Field(default=None, description='最大生成 token 数')
    timeout: int = Field(
        default=300, description='非流式请求超时时间（秒）；大模型长输出需要 2-3 分钟'
    )
    stream_timeout: int | None = Field(
        default=None,
        description=(
            '流式请求首 token 超时时间（秒）。None 表示回退到 timeout。'
            '控制的是：发起流式请求后，最多等多久才能收到第一个 chunk。'
            '建议公有云 API 设 20s，内网 proxy 设 30s，本地 sglang/vLLM 设 60s。'
        ),
    )
    stream_idle_timeout: int = Field(
        default=30,
        description=(
            '流式传输过程中，相邻两次 chunk 之间允许的最大空闲时间（秒）。'
            '一旦开始流式输出，若超过此时间没有新数据则判定超时。'
            '建议 20~60s；与首 token 超时（stream_timeout）无关。'
        ),
    )
    max_retries: int = Field(default=3, description='最大重试次数')
    retry_delay: float = Field(default=1.0, description='重试延迟（秒）')
    use_completion_api: bool = Field(
        default=False, description='使用 Completion API 而非 Chat API'
    )
    thinking_effort: str | None = Field(
        default=None,
        description='统一的 reasoning/thinking 强度配置，供 provider 适配层使用',
    )
    reasoning_protocol: (
        Literal['anthropic_adaptive_thinking', 'openai_reasoning_effort'] | None
    ) = Field(
        default=None,
        description='显式指定 reasoning 请求协议；为空时回退到兼容推断逻辑',
    )
    model_family: str | None = Field(
        default=None,
        description='统一模型族标识，用于能力约束、fallback 分组和请求规范化',
    )
    fallback_group: str | None = Field(
        default=None,
        description='上游代理使用的 fallback 分组名；为空时可由模型族默认值补齐',
    )
    temperature_policy: Literal['default', 'force_one_when_reasoning'] | None = Field(
        default=None,
        description='温度规范化策略；支持按模型族自动收敛 provider 特殊约束',
    )


class LLMResponse(BaseModel):
    """LLM 响应"""

    content: str | None = Field(default=None, description='生成的文本内容')
    reasoning_content: str | None = Field(default=None, description='模型返回的推理内容')
    api_message_extras: dict[str, Any] = Field(
        default_factory=dict,
        description='需要在后续 assistant message 中保留的原始扩展字段',
    )
    tool_calls: list[ToolCall] | None = Field(default=None, description='工具调用列表')
    finish_reason: str | None = Field(default=None, description='结束原因')
    usage: dict[str, int] = Field(default_factory=dict, description='Token 使用统计')
    meta: dict[str, Any] = Field(default_factory=dict, description='其他元数据')

    def to_assistant_message(self) -> AssistantMessage:
        """转换为 AssistantMessage"""
        return AssistantMessage(
            content=self.content,
            tool_calls=self.tool_calls,
            meta={
                **self.meta,
                'finish_reason': self.finish_reason,
                'usage': self.usage,
                'reasoning_content': self.reasoning_content,
                'api_message_extras': self.api_message_extras,
            },
        )


class LLMConfigurationError(RuntimeError):
    """LLM 配置/能力约束错误，适合直接反馈给配置维护者。"""

    def __init__(
        self,
        *,
        category: str,
        message: str,
        raw_error: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.raw_error = raw_error


@dataclass(frozen=True)
class ModelProfile:
    family: str | None
    reasoning_protocol: str | None
    fallback_group: str | None
    temperature_policy: str


_MODEL_FAMILY_DEFAULTS: dict[str, dict[str, str]] = {
    'claude-4.6': {
        'reasoning_protocol': 'anthropic_adaptive_thinking',
        'fallback_group': 'claude-4.6',
        'temperature_policy': 'force_one_when_reasoning',
    },
    'gpt-5': {
        'reasoning_protocol': 'openai_reasoning_effort',
        'fallback_group': 'gpt-5',
        'temperature_policy': 'default',
    },
    'deepseek-reasoner': {
        'reasoning_protocol': 'openai_reasoning_effort',
        'fallback_group': 'deepseek-reasoner',
        'temperature_policy': 'default',
    },
    'gemini-3-flash-preview': {
        'fallback_group': 'gemini-3-flash-preview',
        'temperature_policy': 'default',
    },
}


class BaseLLM(ABC):
    """LLM 基类

    定义统一的 LLM 调用接口。
    """

    def __init__(self, config: LLMConfig, output_config: dict[str, Any] | None = None):
        """初始化 LLM

        Args:
            config: LLM 配置
            output_config: 输出显示配置，包含：
                - show_in_console: 是否在终端显示
                - log_to_file: 是否记录到日志文件
        """
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        self.output_config = output_config or {}
        self.show_in_console = self.output_config.get('show_in_console', False)
        self.log_to_file = self.output_config.get('log_to_file', False)
        # 跟踪已记录的消息数量，用于避免重复记录系统消息和初始任务描述
        self._logged_message_count = 0
        self._setup()

    def _setup(self) -> None:
        """初始化设置，由子类实现"""

    @abstractmethod
    def _call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """调用 LLM API（子类实现）

        Args:
            messages: 消息列表（API 格式）
            tools: 工具规格列表（API 格式）
            **kwargs: 额外参数

        Returns:
            LLM 响应
        """

    def query(
        self,
        dialog: Dialog,
        **kwargs: Any,
    ) -> AssistantMessage:
        """查询 LLM

        Args:
            dialog: 对话对象
            **kwargs: 额外参数（覆盖配置）

        Returns:
            助手消息
        """
        # 转换为 API 格式
        messages = dialog.get_messages_for_api()
        tools = self._convert_tools(dialog.tools) if dialog.tools else None

        # 记录请求（如果启用日志）
        if self.log_to_file:
            self._log_request(messages, tools)

        # 调用 API（带重试）
        response = self._call_with_retry(messages, tools, **kwargs)

        # 记录响应（如果启用日志）
        if self.log_to_file:
            self._log_response(response)

        # 转换为 AssistantMessage
        return response.to_assistant_message()

    def query_stream(
        self,
        dialog: Dialog,
        on_token: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> AssistantMessage:
        """流式查询 LLM，逐 token 调用 on_token(delta)，返回完整 AssistantMessage。

        默认实现：回退到 query()，一次性调用 on_token(full_content)。
        子类可覆盖以实现真正的流式输出。

        Args:
            dialog: 对话对象
            on_token: 每收到一个 token 时调用的回调函数，参数为 token 增量字符串
            **kwargs: 额外参数（覆盖配置）

        Returns:
            完整的助手消息（与 query() 接口兼容）
        """
        result = self.query(dialog, **kwargs)
        if on_token is not None:
            content = result.content if isinstance(result.content, str) else ''
            if content:
                on_token(content)
        return result

    def _log_request(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> None:
        """记录 LLM 请求到日志

        优化：只记录新增的消息，避免重复记录系统消息和初始任务描述。
        第一次请求时记录所有消息，后续请求只记录新增的消息。
        当检测到消息数量减少时（如重置context后），重置计数器并记录所有消息。
        """
        self.logger.info('=' * 80)
        self.logger.info('LLM Request:')
        self.logger.info(f"Model: {self.config.model}")
        if tools:
            self.logger.info(
                f"Tools: {[t.get('function', {}).get('name', 'unknown') for t in tools]}"
            )

        # 检测是否是新对话开始（消息数量减少，通常发生在重置context后）
        if len(messages) <= self._logged_message_count:
            # 消息数量减少，说明是新对话开始，重置计数器
            self.logger.info(
                'New conversation detected (message count decreased), resetting log counter'
            )
            self._logged_message_count = 0

        # 计算需要记录的消息
        new_messages = messages[self._logged_message_count :]

        if self._logged_message_count == 0:
            # 第一次请求，记录所有消息（包括系统消息和初始任务描述）
            self.logger.info('Messages:')
            for i, msg in enumerate(messages):
                self._log_single_message(i + 1, msg)
            self._logged_message_count = len(messages)
        else:
            # 后续请求，只记录新增的消息
            if new_messages:
                self.logger.info(
                    f"New Messages (continuing from message {self._logged_message_count + 1}):"
                )
                for i, msg in enumerate(new_messages):
                    self._log_single_message(self._logged_message_count + i + 1, msg)
                self._logged_message_count = len(messages)
            else:
                # 没有新消息（可能由于上下文截断导致消息数量减少）
                self.logger.info(
                    f"Messages: (same as previous, total: {len(messages)})"
                )
                # 更新已记录的消息数量，避免后续重复
                self._logged_message_count = len(messages)

        self.logger.info('=' * 80)

    def _log_single_message(self, index: int, msg: dict[str, Any]) -> None:
        """记录单条消息，处理工具调用的特殊显示

        Args:
            index: 消息序号
            msg: 消息字典
        """
        role = msg.get('role', 'unknown')
        content = msg.get('content', '')
        tool_calls = msg.get('tool_calls', [])

        # 如果是 assistant 消息且有工具调用
        if role == 'assistant' and tool_calls:
            if content:
                # 有文本内容，先显示内容
                content_display = (
                    truncate_content(content) if isinstance(content, str) else content
                )
                self.logger.info(f"  [{index}] {role}: {content_display}")
            else:
                # 只有工具调用，显示占位符
                self.logger.info(
                    f"  [{index}] {role}: [Calling {len(tool_calls)} tool(s)]"
                )

            # 显示每个工具调用的详细信息
            for i, tc in enumerate(tool_calls):
                if isinstance(tc, dict):
                    func = tc.get('function', {})
                    tool_name = func.get('name', 'unknown')
                    tool_args = func.get('arguments', '')

                    # 格式化参数（如果是 JSON 字符串，尝试解析并美化）
                    try:
                        import json

                        args_dict = (
                            json.loads(tool_args)
                            if isinstance(tool_args, str)
                            else tool_args
                        )
                        args_display = json.dumps(
                            args_dict, indent=2, ensure_ascii=False
                        )
                        # 如果参数太长，截断
                        if len(args_display) > 500:
                            args_display = args_display[:500] + '\n    ... [truncated]'
                    except:
                        args_display = str(tool_args)

                    self.logger.info(f"      Tool #{i+1}: {tool_name}")
                    self.logger.info(f"      Args: {args_display}")
        else:
            # 正常消息（没有工具调用）
            if isinstance(content, str):
                content = truncate_content(content)
            self.logger.info(f"  [{index}] {role}: {content}")

    def _log_response(self, response: LLMResponse) -> None:
        """记录 LLM 响应到日志"""
        self.logger.info('=' * 80)
        self.logger.info('LLM Response:')
        if response.content:
            # 截断过长的内容
            content = truncate_content(response.content)
            self.logger.info(f"Content: {content}")
        if response.tool_calls:
            self.logger.info(
                f"Tool Calls: {[tc.function.name for tc in response.tool_calls]}"
            )
        if response.usage:
            self.logger.info(f"Usage: {response.usage}")
        self.logger.info('=' * 80)

    def _call_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """带重试的调用

        超时异常（ReadTimeout / APITimeoutError）会在每次重试时将 timeout 翻倍，
        避免因模型响应慢而反复以同样的短超时失败。

        Args:
            messages: 消息列表
            tools: 工具列表
            **kwargs: 额外参数（timeout 可在此覆盖配置值）

        Returns:
            LLM 响应
        """
        import traceback

        try:
            import httpx as _httpx

            _TIMEOUT_EXCEPTIONS: tuple[type[BaseException], ...] = (
                _httpx.ReadTimeout,
                _httpx.ConnectTimeout,
                _httpx.PoolTimeout,
            )
        except ImportError:
            _TIMEOUT_EXCEPTIONS = ()

        try:
            import openai as _openai

            _TIMEOUT_EXCEPTIONS = _TIMEOUT_EXCEPTIONS + (_openai.APITimeoutError,)
        except ImportError:
            pass

        last_error = None
        # 首次 timeout 取 kwargs 覆盖值或配置值；每次超时重试后翻倍
        current_timeout: int = int(kwargs.pop('timeout', self.config.timeout))

        for attempt in range(self.config.max_retries):
            try:
                return self._call(messages, tools, timeout=current_timeout, **kwargs)
            except Exception as e:
                traceback.print_exc()
                last_error = e

                err_str = str(e).lower()
                # 上下文超长：无意义重试，直接抛出
                if 'tokens' in err_str and ('exceed' in err_str or 'limit' in err_str):
                    self.logger.error(
                        f"Context length exceeded: {e}. "
                        f"No point retrying with the same oversized context."
                    )
                    raise

                # 工具调用 arguments 非法 JSON：同样无意义重试，直接抛出
                # 此错误由 litellm/Bedrock 适配器在将历史消息转换为 Bedrock 格式时触发，
                # 根因是上一轮 LLM 返回了非法 JSON arguments 并被存入对话历史。
                # 重试不会改变输入，必然再次失败。
                is_malformed_tool_args = (
                    'unable to convert openai tool calls' in err_str
                    or ("expecting ':' delimiter" in err_str)
                    or (
                        'json' in err_str
                        and 'tool' in err_str
                        and 'argument' in err_str
                    )
                )
                if is_malformed_tool_args:
                    self.logger.error(
                        'Non-retryable error: malformed tool call arguments in message '
                        'history (litellm/Bedrock JSON parse failure). '
                        'Aborting retries immediately. Error: %s',
                        e,
                    )
                    raise

                classified_error = _classify_llm_error(self.config, e)
                if classified_error is not None:
                    self.logger.error(
                        'Non-retryable LLM configuration error [%s]: %s | raw=%s',
                        classified_error.category,
                        classified_error,
                        classified_error.raw_error or e,
                    )
                    raise classified_error from e

                is_timeout = (
                    isinstance(e, _TIMEOUT_EXCEPTIONS) if _TIMEOUT_EXCEPTIONS else False
                )

                if is_timeout:
                    next_timeout = current_timeout * 2
                    self.logger.warning(
                        f"LLM call timed out after {current_timeout}s "
                        f"(attempt {attempt + 1}/{self.config.max_retries}). "
                        f"Retrying with timeout={next_timeout}s. Error: {e}"
                    )
                    current_timeout = next_timeout
                else:
                    self.logger.warning(
                        f"traceback: {traceback.format_exc()}"
                        f"LLM call failed (attempt {attempt + 1}/{self.config.max_retries}): {e}"
                    )

                if attempt < self.config.max_retries - 1:
                    delay = self.config.retry_delay * (2**attempt)  # 指数退避
                    time.sleep(delay)

        # 所有重试失败
        raise RuntimeError(
            f"LLM call failed after {self.config.max_retries} attempts"
        ) from last_error

    def _convert_tools(self, tool_specs: list) -> list[dict[str, Any]]:
        """转换工具规格为 API 格式

        Args:
            tool_specs: ToolSpec 列表

        Returns:
            API 格式的工具列表
        """
        return [spec.model_dump() for spec in tool_specs]


def _is_azure_base_url(base_url: str | None) -> bool:
    """判断 base_url 是否为 Azure OpenAI 端点"""
    if not base_url:
        return False
    return 'openai.azure.com' in base_url


def _azure_deployment_name(model: str) -> str:
    """从配置的 model 中取出 Azure 部署名（去掉 azure/ 前缀）"""
    s = (model or '').strip()
    if s.startswith('azure/'):
        return s[6:].strip() or model
    return s


def _build_anthropic_adaptive_thinking_request(effort: str) -> dict[str, Any]:
    return {
        'extra_body': {
            'thinking': {'type': 'adaptive'},
            'output_config': {'effort': effort},
        }
    }


def _build_openai_reasoning_effort_request(effort: str) -> dict[str, Any]:
    return {'reasoning_effort': effort}


_REASONING_PROTOCOL_BUILDERS: dict[str, Callable[[str], dict[str, Any]]] = {
    'anthropic_adaptive_thinking': _build_anthropic_adaptive_thinking_request,
    'openai_reasoning_effort': _build_openai_reasoning_effort_request,
}


def _infer_model_family_from_model(model: str) -> str | None:
    model_name = (model or '').strip().lower()
    if 'claude-sonnet-4-6' in model_name or 'claude-opus-4-6' in model_name:
        return 'claude-4.6'
    if 'gpt-5' in model_name:
        return 'gpt-5'
    if 'deepseek-reasoner' in model_name:
        return 'deepseek-reasoner'
    if 'gemini-3-flash-preview' in model_name:
        return 'gemini-3-flash-preview'
    return None


def _infer_reasoning_protocol_from_model(model: str) -> str | None:
    family = _infer_model_family_from_model(model)
    family_defaults = _MODEL_FAMILY_DEFAULTS.get(family or '', {})
    if family_defaults.get('reasoning_protocol'):
        return family_defaults['reasoning_protocol']
    if (model or '').strip():
        return 'openai_reasoning_effort'
    return None


def _resolve_model_profile(config: LLMConfig) -> ModelProfile:
    family = config.model_family or _infer_model_family_from_model(config.model)
    defaults = _MODEL_FAMILY_DEFAULTS.get(family or '', {})
    return ModelProfile(
        family=family,
        reasoning_protocol=(
            config.reasoning_protocol
            or defaults.get('reasoning_protocol')
            or _infer_reasoning_protocol_from_model(config.model)
        ),
        fallback_group=config.fallback_group or defaults.get('fallback_group'),
        temperature_policy=(
            config.temperature_policy
            or defaults.get('temperature_policy')
            or 'default'
        ),
    )


def _resolve_reasoning_protocol(config: LLMConfig) -> str | None:
    return _resolve_model_profile(config).reasoning_protocol


def _build_reasoning_request_overrides(config: LLMConfig) -> dict[str, Any]:
    """构造 reasoning/thinking 相关的 provider 请求覆盖参数。"""
    effort = (config.thinking_effort or '').strip().lower()
    if not effort:
        return {}

    protocol = _resolve_reasoning_protocol(config)
    if not protocol:
        return {}

    builder = _REASONING_PROTOCOL_BUILDERS.get(protocol)
    if builder is None:
        raise ValueError(f'Unsupported reasoning protocol: {protocol}')
    return builder(effort)


def _normalize_request_params(
    config: LLMConfig, request_params: dict[str, Any]
) -> dict[str, Any]:
    """按统一模型画像规范化请求参数，优先在本地消化 provider 特殊约束。"""
    normalized = request_params.copy()
    profile = _resolve_model_profile(config)
    thinking_enabled = bool((config.thinking_effort or '').strip())
    if (
        thinking_enabled
        and profile.reasoning_protocol == 'anthropic_adaptive_thinking'
        and profile.temperature_policy == 'force_one_when_reasoning'
    ):
        normalized['temperature'] = 1
    return normalized


def _classify_llm_error(
    config: LLMConfig, error: Exception
) -> LLMConfigurationError | None:
    """将长 provider 错误归一为高信号、不可重试的配置错误。"""
    err_text = str(error)
    err_lower = err_text.lower()
    issues: list[str] = []
    profile = _resolve_model_profile(config)

    if (
        'temperature' in err_lower
        and 'may only be set to 1' in err_lower
        and profile.reasoning_protocol == 'anthropic_adaptive_thinking'
    ):
        issues.append(
            '当前模型在 thinking/adaptive 模式下 temperature 必须为 1；'
            '请通过统一模型策略规范化温度，避免把其他采样值直接发给上游。'
        )

    if 'contentblock object' in err_lower and 'is blank' in err_lower:
        issues.append(
            '回放的 assistant 历史中包含空的 text/thinking 内容块；'
            '请在发送前过滤空 content 与空 reasoning block，避免 Bedrock/Anthropic 拒绝该消息。'
        )

    if 'no fallback model group found' in err_lower:
        group = profile.fallback_group
        if group:
            issues.append(
                f'上游代理未识别 fallback_group={group}；'
                '请检查代理侧 fallback 分组是否与本地模型画像一致。'
            )
        else:
            issues.append(
                '当前模型未声明 fallback_group；请在统一模型配置中补齐该字段。'
            )

    if not issues:
        return None

    model_label = profile.family or config.model
    return LLMConfigurationError(
        category='model_configuration_error',
        message=f"LLM 配置错误（{model_label}）：{' '.join(issues)}",
        raw_error=err_text,
    )


def _merge_request_overrides(
    request_params: dict[str, Any], overrides: dict[str, Any]
) -> dict[str, Any]:
    """合并 reasoning 请求覆盖，保留已有 extra_body 字段。"""
    if not overrides:
        return request_params

    merged = request_params.copy()
    for key, value in overrides.items():
        if (
            key == 'extra_body'
            and isinstance(value, dict)
            and isinstance(merged.get('extra_body'), dict)
        ):
            merged['extra_body'] = {
                **merged['extra_body'],
                **value,
            }
        else:
            merged[key] = value
    return merged


def _merge_api_message_extras(
    current: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any]:
    """递归合并 assistant 原始扩展字段，用于多轮回放。"""
    if not incoming:
        return current

    merged = dict(current)
    for key, value in incoming.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge_api_message_extras(existing, value)
        elif isinstance(existing, list) and isinstance(value, list):
            merged[key] = [*existing, *value]
        else:
            merged[key] = value
    return merged


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

        try:
            stream = self.client.chat.completions.create(**request_params)
            for chunk in stream:
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
                reasoning_delta = getattr(delta, 'reasoning_content', None)
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
                'finish_reason': finish_reason,
                'reasoning_content': ''.join(full_reasoning_content) or None,
                'api_message_extras': assistant_extra_acc,
            },
        )


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
                parts.append(f"<｜User｜> {content} <｜Assistant｜>")
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

        try:
            stream = self.client.chat.completions.create(**request_params)
            for chunk in stream:
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
                reasoning_delta = getattr(delta, 'reasoning_content', None)
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
                'finish_reason': finish_reason,
                'reasoning_content': ''.join(full_reasoning_content) or None,
                'api_message_extras': assistant_extra_acc,
            },
        )


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
                import json

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
        try:
            with self.client.messages.stream(**request_params) as stream:
                for text in stream.text_stream:
                    if text:
                        full_content.append(text)
                        if on_token is not None:
                            on_token(text)
        except Exception as e:
            self.logger.warning(
                'Anthropic stream failed, falling back to query(): %s', e
            )
            return super().query_stream(dialog, on_token=on_token, **kwargs)

        return AssistantMessage(content=''.join(full_content))


def create_llm(
    config: LLMConfig, output_config: dict[str, Any] | None = None
) -> BaseLLM:
    """LLM 工厂函数

    Args:
        config: LLM 配置
        output_config: 输出显示配置

    Returns:
        LLM 实例

    Raises:
        ValueError: 不支持的提供商
    """
    if config.provider == 'openai' or config.provider == 'openrouter':
        return OpenAILLM(config, output_config=output_config)
    elif config.provider == 'anthropic':
        return AnthropicLLM(config, output_config=output_config)
    elif config.provider == 'deepseek':
        return DeepSeekLLM(config, output_config=output_config)
    else:
        raise ValueError(f"Unsupported LLM provider: {config.provider}")
