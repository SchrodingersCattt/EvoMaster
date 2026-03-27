"""可插拔的 Token 计数抽象。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from evomaster.utils.types import Dialog, Message


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
