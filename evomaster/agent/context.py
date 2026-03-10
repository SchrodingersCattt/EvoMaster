"""EvoMaster Agent 上下文管理

提供上下文管理功能，包括对话历史管理、上下文窗口控制、历史压缩等。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from evomaster.utils.types import Dialog, Message
else:
    from evomaster.utils.types import Dialog, Message


class TruncationStrategy(str, Enum):
    """历史截断策略"""

    NONE = 'none'  # 不截断
    LATEST_HALF = 'latest_half'  # 保留最新一半
    SLIDING_WINDOW = 'sliding_window'  # 滑动窗口
    SUMMARY = 'summary'  # 摘要压缩


class ContextConfig(BaseModel):
    """上下文管理配置"""

    max_tokens: int = Field(default=128000, description='最大 token 数')
    truncation_strategy: TruncationStrategy = Field(
        default=TruncationStrategy.LATEST_HALF, description='截断策略'
    )
    preserve_system_messages: bool = Field(default=True, description='是否保留系统消息')
    preserve_recent_turns: int = Field(default=5, description='保留最近的对话轮数')


class ContextManager:
    """上下文管理器

    负责管理对话上下文，包括：
    - 上下文窗口大小控制
    - 历史消息截断和压缩
    - Token 计数（可扩展）
    """

    def __init__(self, config: ContextConfig | None = None):
        self.config = config or ContextConfig()
        self._token_counter: TokenCounter | None = None

    def set_token_counter(self, counter: TokenCounter) -> None:
        """设置 token 计数器"""
        self._token_counter = counter

    def estimate_tokens(self, dialog: Dialog) -> int:
        """估算对话的 token 数

        如果设置了 token 计数器，使用计数器；否则使用简单估算。
        """
        if self._token_counter:
            return self._token_counter.count_dialog(dialog)

        # 保守估算：约3个字符 = 1 token（对中英文混合内容更准确）
        # Bug fix: 计算所有消息的完整字符数（包括 tool_calls 参数）
        total_chars = sum(
            self._content_char_len(msg.content) for msg in dialog.messages
        )

        # Bug fix: 计算工具定义的字符数（每次 API 调用都会发送）
        total_chars += self._tools_char_len(dialog.tools)

        return int(total_chars / 3)

    @staticmethod
    def _content_char_len(content: str | list | dict | None) -> int:
        """消息 content 的字符长度（多模态时只计 text 块）。"""
        if content is None:
            return 0
        if isinstance(content, str):
            return len(content)
        if isinstance(content, list):
            return sum(
                len(b.get('text', ''))
                for b in content
                if isinstance(b, dict) and b.get('type') == 'text'
            )
        return len(str(content))

    def should_truncate(self, dialog: Dialog) -> bool:
        """判断是否需要截断"""
        return self.estimate_tokens(dialog) > self.config.max_tokens

    def truncate(self, dialog: Dialog) -> Dialog:
        """根据策略截断对话历史

        Returns:
            截断后的新 Dialog 对象
        """
        if self.config.truncation_strategy == TruncationStrategy.NONE:
            return dialog
        elif self.config.truncation_strategy == TruncationStrategy.LATEST_HALF:
            return self._truncate_latest_half(dialog)
        elif self.config.truncation_strategy == TruncationStrategy.SLIDING_WINDOW:
            return self._truncate_sliding_window(dialog)
        elif self.config.truncation_strategy == TruncationStrategy.SUMMARY:
            return self._truncate_with_summary(dialog)
        else:
            return dialog

    def _truncate_latest_half(self, dialog: Dialog) -> Dialog:
        """保留最新一半的历史

        保留系统消息和用户初始消息，然后保留最近一半的对话。
        确保截断后的消息序列始终合法（tool 消息前必有 assistant）。
        
        按 turn 为单位截断：一个 turn = 1 个 assistant + 其所有 tool 消息。
        """
        messages = dialog.messages

        # 找到第一个 assistant 消息的位置
        assistant_start = 0
        for i, msg in enumerate(messages):
            if msg.role.value == 'assistant':
                assistant_start = i
                break

        # 计算需要保留的消息数量
        num_messages = len(messages)
        num_to_truncate = num_messages - assistant_start
        num_to_preserve = num_to_truncate // 2
        preserve_start = num_messages - num_to_preserve

        # 如果 preserve_start 指向的是 tool 消息，需要调整到一个合法的 turn 边界
        if preserve_start < num_messages and messages[preserve_start].role.value == 'tool':
            # 往前找到这个 tool 所属的 assistant 消息
            turn_start = preserve_start
            while (
                turn_start > assistant_start
                and messages[turn_start].role.value != 'assistant'
            ):
                turn_start -= 1
            # 现在 turn_start 指向该 tool 所属的 assistant
            # 从这个 assistant 开始保留整个 turn（包括所有 tool 消息）
            preserve_start = turn_start

        # 如果往前找到了 assistant_start 还没找到 assistant
        # 说明无法有效截断，返回原对话
        if preserve_start <= assistant_start:
            return dialog

        # 构建新对话：保留 system/user 前缀 + 从找到的 assistant 开始的后续消息
        new_messages = messages[:assistant_start] + messages[preserve_start:]

        return Dialog(
            messages=new_messages,
            tools=dialog.tools,
            meta={**dialog.meta, 'truncated': True, 'strategy': 'latest_half'},
        )

    def _truncate_sliding_window(self, dialog: Dialog) -> Dialog:
        """滑动窗口截断

        保留系统消息和最近 N 轮对话。
        """
        messages = dialog.messages
        preserve_turns = self.config.preserve_recent_turns

        # 分离系统消息和其他消息
        system_messages: list[Message] = []
        other_messages: list[Message] = []

        for msg in messages:
            if msg.role.value == 'system':
                system_messages.append(msg)
            else:
                other_messages.append(msg)

        # 计算需要保留的消息数（每轮约 2-3 条消息）
        keep_count = preserve_turns * 3
        if len(other_messages) <= keep_count:
            return dialog

        # 保留最近的消息
        new_messages = system_messages + other_messages[-keep_count:]

        return Dialog(
            messages=new_messages,
            tools=dialog.tools,
            meta={**dialog.meta, 'truncated': True, 'strategy': 'sliding_window'},
        )

    def _truncate_with_summary(self, dialog: Dialog) -> Dialog:
        """摘要压缩（暂未实现）

        将历史对话压缩为摘要，需要 LLM 支持。
        """
        # TODO: 实现摘要压缩，需要 LLM 支持
        # 暂时回退到 latest_half 策略
        return self._truncate_latest_half(dialog)

    def prepare_for_query(self, dialog: Dialog) -> Dialog:
        """为 LLM 查询准备对话

        检查并在必要时截断对话。
        """
        if self.should_truncate(dialog):
            return self.truncate(dialog)
        return dialog


class TokenCounter(ABC):
    """Token 计数器抽象基类"""

    @abstractmethod
    def count_text(self, text: str) -> int:
        """计算文本的 token 数"""

    @abstractmethod
    def count_message(self, message: Message) -> int:
        """计算单条消息的 token 数"""

    def count_dialog(self, dialog: Dialog) -> int:
        """计算对话的总 token 数"""
        return sum(self.count_message(msg) for msg in dialog.messages)


class SimpleTokenCounter(TokenCounter):
    """简单的 Token 计数器

    基于字符数的简单估算。
    """

    def __init__(self, chars_per_token: float = 4.0):
        self.chars_per_token = chars_per_token

    def count_text(self, text: str) -> int:
        return int(len(text) / self.chars_per_token)

    def count_message(self, message: Message) -> int:
        content = message.content
        if isinstance(content, list):
            # 多模态内容块：只计 text 块长度
            total_chars = sum(
                len(b.get('text', ''))
                for b in content
                if isinstance(b, dict) and b.get('type') == 'text'
            )
            content_tokens = int(total_chars / self.chars_per_token)
        else:
            content_tokens = self.count_text(content or '')
        overhead = 4
        return content_tokens + overhead
