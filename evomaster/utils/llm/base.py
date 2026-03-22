"""LLM 抽象基类与重试逻辑。"""

from __future__ import annotations

import json
import logging
import time
import traceback
from abc import ABC, abstractmethod
from typing import Any, Callable

from evomaster.utils.types import AssistantMessage, Dialog

from .config_models import LLMConfig, LLMResponse
from .helpers import _classify_llm_error
from .sanitize import truncate_content


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
                    except Exception:
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

                is_orphaned_tool_use = (
                    ('tool_use' in err_str and 'tool_result' in err_str)
                    or ('tool_use' in err_str and 'without' in err_str)
                    or (
                        # Bedrock 原始错误格式：
                        # "tool_use ids were found without tool_result blocks"
                        'ids were found without' in err_str
                        and 'tool' in err_str
                    )
                )
                if is_orphaned_tool_use:
                    self.logger.error(
                        'Non-retryable error: orphaned tool_use block(s) in message '
                        'history — each tool_use must have a corresponding tool_result '
                        'in the next message (Claude/Bedrock constraint). '
                        'This is a dialog structure issue; retrying will not help. '
                        'Error: %s',
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
