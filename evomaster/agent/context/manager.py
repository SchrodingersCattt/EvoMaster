"""上下文管理器：窗口控制、截断、压缩与 token 估算。"""

from __future__ import annotations

import logging
from typing import Callable

from evomaster.utils.types import Dialog, Message

from .compactor import ContextCompactor
from .config import ContextConfig, TruncationStrategy
from .token_counter import TokenCounter
from .truncation import safe_tail

_logger = logging.getLogger(__name__)


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
        # ContextCompactor 实例（由外部注入，默认 None）
        self._compactor: ContextCompactor | None = None
        # compact 生命周期事件回调（由 StreamingMatMasterAgent 注入）
        self._on_compact_event: Callable[[dict], None] | None = None

    def set_token_counter(self, counter: TokenCounter) -> None:
        """设置 token 计数器"""
        self._token_counter = counter

    def set_compactor(self, compactor: ContextCompactor) -> None:
        """注入 ContextCompactor 实例（由 MatMasterAgent.__init__ 调用）。"""
        self._compactor = compactor

    def set_compact_event_callback(self, cb: Callable[[dict], None]) -> None:
        """注入 compact 生命周期事件回调（由 StreamingMatMasterAgent.__init__ 调用）。

        cb(payload) 在 compact started/finished/skipped/failed 时被调用。
        payload 结构：{'type': 'context_compaction', 'status': ..., ...}
        """
        self._on_compact_event = cb

    def should_compact(self, dialog: Dialog) -> bool:
        """判断是否需要 compact（比 should_truncate 更早触发，仅按 token 数判断）。

        P0: trigger_tokens 改为动态计算（context_window_tokens * trigger_ratio）。
        """
        cfg = self.config.compaction
        if not cfg.enabled:
            return False
        effective_trigger = cfg.effective_trigger_tokens()
        return self.estimate_tokens(dialog) > effective_trigger

    def estimate_tokens(self, dialog: Dialog) -> int:
        """估算对话的 token 数

        如果设置了 token 计数器，使用计数器；否则使用简单估算。
        """
        if self._token_counter:
            return self._token_counter.count_dialog(dialog)

        # 保守估算：约3个字符 = 1 token（对中英文混合内容更准确）
        # Bug fix: 计算所有消息的完整字符数（包括 tool_calls 参数）
        total_chars = sum(self._message_char_len(msg) for msg in dialog.messages)

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

    @staticmethod
    def _message_char_len(msg: Message) -> int:
        """计算单条消息的字符数，包括 content 和 tool_calls 参数。"""
        import json

        total = 0
        # content 字段
        content = msg.content
        if content is None:
            pass
        elif isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            total += sum(
                len(b.get('text', ''))
                for b in content
                if isinstance(b, dict) and b.get('type') == 'text'
            )
        else:
            total += len(str(content))
        # tool_calls 字段（assistant 消息调用工具时的参数）
        tool_calls = getattr(msg, 'tool_calls', None)
        if tool_calls:
            for tc in tool_calls:
                func = getattr(tc, 'function', None)
                if func:
                    total += len(getattr(func, 'name', '') or '')
                    args = getattr(func, 'arguments', None)
                    if args:
                        total += len(
                            args if isinstance(args, str) else json.dumps(args)
                        )
        return total

    @staticmethod
    def _tools_char_len(tools: list | None) -> int:
        """计算工具定义的字符数（每次 API 调用都会随 tools 参数发送）。"""
        import json

        if not tools:
            return 0
        try:
            return len(
                json.dumps(
                    [t.model_dump() if hasattr(t, 'model_dump') else t for t in tools]
                )
            )
        except Exception:
            return sum(len(str(t)) for t in tools)

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
        """保留最新一半的历史。

        保留所有 system 消息；若首条非 system 消息为用户（通常为任务初始 user），则固定保留。
        对其余消息按条数保留后半段（与「最新一半」语义一致）。

        说明：旧实现按 assistant 条数取 safe_tail 的「轮数」，但 safe_tail 将每条 user
        单独计为一轮，导致轮数语义不一致，且会丢掉初始化时的首条 user。
        """
        messages = dialog.messages

        # 分离 system 和非 system 消息
        system_msgs: list[Message] = []
        other_msgs: list[Message] = []
        for msg in messages:
            if msg.role.value == 'system':
                system_msgs.append(msg)
            else:
                other_msgs.append(msg)

        if not other_msgs:
            return dialog

        # 与 compaction 一致：固定保留 system 之后的第一条 user（任务初始提示）
        pinned: list[Message] = []
        rest = other_msgs
        if other_msgs[0].role.value == 'user':
            pinned = [other_msgs[0]]
            rest = other_msgs[1:]

        if not rest:
            return dialog

        keep_n = max(1, len(rest) // 2)
        tail = rest[-keep_n:]

        if len(tail) >= len(rest):
            return dialog

        new_messages = system_msgs + pinned + tail
        return Dialog(
            messages=new_messages,
            tools=dialog.tools,
            meta={**dialog.meta, 'truncated': True, 'strategy': 'latest_half'},
        )

    def _truncate_sliding_window(self, dialog: Dialog) -> Dialog:
        """滑动窗口截断（按完整 tool-transaction 为单位保留最近 N 轮）。"""
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

        tail = safe_tail(other_messages, preserve_turns)
        if len(tail) >= len(other_messages):
            return dialog

        new_messages = system_messages + tail
        return Dialog(
            messages=new_messages,
            tools=dialog.tools,
            meta={**dialog.meta, 'truncated': True, 'strategy': 'sliding_window'},
        )

    def _truncate_with_summary(self, dialog: Dialog) -> Dialog:
        """LLM 摘要压缩。

        compaction.enabled=False 或无 compactor 时降级 latest_half。
        """
        cfg = self.config.compaction
        if not cfg.enabled or self._compactor is None:
            return self._truncate_latest_half(dialog)
        try:
            return self._compactor.compact(
                dialog,
                context_manager=self,
                on_event=self._on_compact_event,
            )
        except Exception as e:
            _logger.warning(
                'ContextCompactor.compact failed (%s); falling back to %s',
                e,
                cfg.fallback_strategy,
            )
            if cfg.fallback_strategy == 'sliding_window':
                return self._truncate_sliding_window(dialog)
            return self._truncate_latest_half(dialog)

    def prepare_for_query(self, dialog: Dialog) -> Dialog:
        """为 LLM 查询准备对话

        检查并在必要时截断对话。compact 优先于 truncate（token 阈值更低，更早触发）。
        循环直到 token 数低于限制或无法继续压缩/截断。

        should_compact 使用动态 trigger_tokens（context_window_tokens * trigger_ratio）。
        """
        tokens_entry = self.estimate_tokens(dialog)
        _logger.debug('prepare_for_query: entry tokens=%d', tokens_entry)

        # compact 优先于 truncate
        max_iterations = 10
        for _ in range(max_iterations):
            # compact 优先（token 阈值更低，在 truncate 之前触发）
            if self.should_compact(dialog):
                compacted = self._truncate_with_summary(dialog)
                if len(compacted.messages) < len(dialog.messages):
                    dialog = compacted
                    continue
                # compact 没有减少消息数（被跳过或无效），不再重试 compact
                _logger.debug(
                    'prepare_for_query: compact did not reduce messages (skipped or ineffective), '
                    'falling through to truncate'
                )
            if not self.should_truncate(dialog):
                break
            truncated = self.truncate(dialog)
            # 如果截断后消息数没有减少，说明无法继续截断，退出
            if len(truncated.messages) >= len(dialog.messages):
                break
            dialog = truncated

        tokens_exit = self.estimate_tokens(dialog)
        if tokens_exit != tokens_entry:
            _logger.info(
                'prepare_for_query: tokens %d → %d (saved %d)',
                tokens_entry,
                tokens_exit,
                tokens_entry - tokens_exit,
            )
        return dialog
