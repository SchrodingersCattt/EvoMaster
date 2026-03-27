"""上下文相关枚举与 Pydantic 配置。"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class TruncationStrategy(str, Enum):
    """历史截断策略"""

    NONE = 'none'  # 不截断
    LATEST_HALF = 'latest_half'  # 保留最新一半
    SLIDING_WINDOW = 'sliding_window'  # 滑动窗口
    SUMMARY = 'summary'  # 摘要压缩


class CompactionConfig(BaseModel):
    """Compact message 触发与行为配置"""

    enabled: bool = Field(
        default=False, description='是否启用 compact message（默认关闭）'
    )
    # trigger_tokens 默认 -1 表示使用 context_window_tokens * trigger_ratio 动态计算
    trigger_tokens: int = Field(
        default=-1,
        description=(
            '超过此 token 数触发压缩。'
            '-1（默认）表示自动按 context_window_tokens * trigger_ratio 计算。'
            '显式设置正整数时直接使用该值（向后兼容）。'
        ),
    )
    # P0: 新增 context_window_tokens 和 trigger_ratio
    context_window_tokens: int = Field(
        default=200000,
        description='模型 context window 大小（tokens）。用于动态计算 trigger_tokens。',
    )
    trigger_ratio: float = Field(
        default=0.80,
        ge=0.1,
        le=0.99,
        description='trigger_tokens = context_window_tokens * trigger_ratio（默认 0.80 = 80%%）',
    )
    preserve_recent_turns: int = Field(
        default=2, description='压缩后保留最近 N 轮完整 tool-transaction'
    )
    compaction_llm: str | None = Field(
        default=None, description='用于摘要的 LLM key；None 则复用 agent LLM'
    )
    fallback_strategy: str = Field(
        default='sliding_window', description='压缩失败时的降级策略'
    )
    max_compact_tokens: int = Field(
        default=3000, description='compact message 最大 token 数（约 9000 字符）'
    )
    # 无效 compact 检测阈值：可压缩部分占比低于此值时跳过压缩
    min_compressible_ratio: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description=(
            'compressible tokens / total tokens 低于此比例时跳过 compact（避免无效压缩）。'
            '默认 0.10：可压缩部分不足总 token 的 10%% 时跳过。'
        ),
    )

    def effective_trigger_tokens(self) -> int:
        """返回实际生效的 trigger_tokens。

        - trigger_tokens > 0：直接使用（向后兼容旧配置）
        - trigger_tokens <= 0：动态计算 context_window_tokens * trigger_ratio
        """
        if self.trigger_tokens > 0:
            return self.trigger_tokens
        return int(self.context_window_tokens * self.trigger_ratio)


class ContextConfig(BaseModel):
    """上下文管理配置"""

    max_tokens: int = Field(default=128000, description='最大 token 数')
    truncation_strategy: TruncationStrategy = Field(
        default=TruncationStrategy.SUMMARY, description='截断策略'
    )
    preserve_system_messages: bool = Field(default=True, description='是否保留系统消息')
    preserve_recent_turns: int = Field(default=5, description='保留最近的对话轮数')
    compaction: CompactionConfig = Field(
        default_factory=CompactionConfig, description='Compact message 配置'
    )
