"""Agent 上下文：配置、按 transaction 截尾、压缩与 Token 计数。"""

from .compactor import CompactionError, ContextCompactor
from .config import CompactionConfig, ContextConfig, TruncationStrategy
from .manager import ContextManager
from .token_counter import SimpleTokenCounter, TokenCounter

__all__ = [
    'CompactionConfig',
    'CompactionError',
    'ContextCompactor',
    'ContextConfig',
    'ContextManager',
    'SimpleTokenCounter',
    'TokenCounter',
    'TruncationStrategy',
]
