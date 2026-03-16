"""EvoMaster Agent 上下文管理

提供上下文管理功能，包括对话历史管理、上下文窗口控制、历史压缩等。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, Callable

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from evomaster.utils.types import AssistantMessage, Dialog, Message
else:
    from evomaster.utils.types import AssistantMessage, Dialog, Message

_logger = logging.getLogger(__name__)


class TruncationStrategy(str, Enum):
    """历史截断策略"""

    NONE = 'none'  # 不截断
    LATEST_HALF = 'latest_half'  # 保留最新一半
    SLIDING_WINDOW = 'sliding_window'  # 滑动窗口
    SUMMARY = 'summary'  # 摘要压缩


# ---------------------------------------------------------------------------
# C.1 — CompactionConfig
# ---------------------------------------------------------------------------

class CompactionConfig(BaseModel):
    """Compact message 触发与行为配置"""

    enabled: bool = Field(default=False, description='是否启用 compact message（默认关闭）')
    trigger_tokens: int = Field(default=80000, description='超过此 token 数触发压缩（唯一触发条件）')
    preserve_recent_turns: int = Field(
        default=2, description='压缩后保留最近 N 轮完整 tool-transaction'
    )
    compaction_llm: str | None = Field(
        default=None, description='用于摘要的 LLM key；None 则复用 agent LLM'
    )
    fallback_strategy: str = Field(default='sliding_window', description='压缩失败时的降级策略')
    max_compact_tokens: int = Field(
        default=3000, description='compact message 最大 token 数（约 9000 字符）'
    )


class ContextConfig(BaseModel):
    """上下文管理配置"""

    max_tokens: int = Field(default=128000, description='最大 token 数')
    truncation_strategy: TruncationStrategy = Field(
        default=TruncationStrategy.SUMMARY, description='截断策略'
    )
    preserve_system_messages: bool = Field(default=True, description='是否保留系统消息')
    preserve_recent_turns: int = Field(default=5, description='保留最近的对话轮数')
    # C.3 — 新增 compaction 字段
    compaction: CompactionConfig = Field(
        default_factory=CompactionConfig, description='Compact message 配置'
    )


# ---------------------------------------------------------------------------
# C.2 — ContextCompactor
# ---------------------------------------------------------------------------

class CompactionError(Exception):
    """Raised when ContextCompactor.compact() fails."""


class ContextCompactor:
    """LLM 驱动的对话历史压缩器。

    compact() 方法：
    1. 从 dialog 中提取 system 消息（保留）+ 最近 preserve_recent_turns 轮（保留）
    2. 把中间历史 + execution_journal.get_compact_summary(include_details=True) 发给 LLM 生成摘要
    3. 摘要格式强制包含：
       - ## Summary of Prior Work
       - ## Produced Artifacts（文件路径列表，从 execution_journal 注入，防幻觉）
       - ## Key Findings
       - ## Current Status
    4. 返回新 Dialog：[system messages] + [COMPACT CONTEXT system msg] + [recent turns]
    """

    # Compact message 格式模板（注入到 prompt 中强制 LLM 遵守）
    _COMPACT_SCHEMA = """\
[COMPACT CONTEXT — Steps 1-{n_compressed}]

## Summary of Prior Work
{{2-3 sentences summarizing completed work}}

## Produced Artifacts
{artifacts_block}

## Key Findings
- {{key finding, ≤80 chars each}}

## Current Status
Step {n_compressed} completed. Next: Step {n_next} — {{next_goal}}

## Errors / Warnings
{{list failed steps with reasons; omit section if none}}"""

    def __init__(
        self,
        config: CompactionConfig,
        llm_caller: Callable[['Dialog'], 'AssistantMessage'],
        execution_journal: object | None = None,
    ) -> None:
        self._config = config
        self._llm_caller = llm_caller
        self._execution_journal = execution_journal

    def compact(self, dialog: 'Dialog') -> 'Dialog':
        """压缩对话历史，返回新 Dialog。失败时抛出 CompactionError。

        保留尾部使用 ContextManager._safe_tail() 以完整 tool-transaction 为单位，
        确保保留段不含孤立 ToolMessage 或不完整的 assistant/tool 配对。
        """
        from evomaster.utils.types import SystemMessage, UserMessage

        messages = dialog.messages
        preserve_turns = self._config.preserve_recent_turns

        # 1. 分离 system 消息和非 system 消息
        system_msgs: list[Message] = []
        other_msgs: list[Message] = []
        for msg in messages:
            if msg.role.value == 'system':
                system_msgs.append(msg)
            else:
                other_msgs.append(msg)

        # 2. 用 _safe_tail 取最近 preserve_turns 个完整 tool-transaction 作为保留尾部
        recent_msgs = ContextManager._safe_tail(other_msgs, preserve_turns)
        if len(recent_msgs) >= len(other_msgs):
            # 没有足够消息可压缩，直接返回原 dialog
            return dialog

        msgs_to_compress = other_msgs[: len(other_msgs) - len(recent_msgs)]

        # 3. 构建 artifacts block（防幻觉：由 Python 注入，LLM 只能引用）
        artifacts_block = self._build_artifacts_block()

        # 4. 构建压缩 prompt
        n_compressed = len(msgs_to_compress)
        n_next = n_compressed + 1
        compaction_prompt = self._build_compaction_prompt(
            msgs_to_compress, artifacts_block, n_compressed, n_next
        )

        # 5. 调用 LLM 生成摘要
        try:
            compaction_dialog = type(dialog)(
                messages=system_msgs + [UserMessage(content=compaction_prompt)],
                tools=[],
            )
            reply = self._llm_caller(compaction_dialog)
            compact_text = (reply.content or '').strip()
            if not compact_text:
                raise CompactionError('LLM returned empty compact message')
        except CompactionError:
            raise
        except Exception as e:
            raise CompactionError(f'LLM call failed: {e}') from e

        # 6. 截断 compact_text 到 max_compact_tokens（按字符估算：1 token ≈ 3 chars）
        max_chars = self._config.max_compact_tokens * 3
        if len(compact_text) > max_chars:
            compact_text = compact_text[:max_chars] + '\n...(compact message truncated)'

        # 7. 构建新 Dialog：system msgs + COMPACT CONTEXT system msg + recent turns
        compact_sys_msg = SystemMessage(
            content=f'[COMPACT CONTEXT]\n\n{compact_text}'
        )
        new_messages = system_msgs + [compact_sys_msg] + recent_msgs
        _logger.debug(
            'ContextCompactor.compact: compressed %d messages → compact_msg (%d chars) + %d recent',
            n_compressed,
            len(compact_text),
            len(recent_msgs),
        )
        return type(dialog)(
            messages=new_messages,
            tools=dialog.tools,
            meta={**dialog.meta, 'compacted': True, 'compressed_count': n_compressed},
        )

    def _build_artifacts_block(self) -> str:
        """从 execution_journal 提取已产出文件路径列表（防幻觉注入）。"""
        if self._execution_journal is None:
            return '(no artifacts recorded)'
        try:
            entries = getattr(self._execution_journal, 'entries', [])
            files: list[str] = []
            for e in entries:
                if e.get('saved_path'):
                    files.append(e['saved_path'])
                for f in e.get('downloaded_files') or []:
                    files.append(f)
            if not files:
                return '(no files produced yet)'
            # 最多列出最近 20 个
            recent = files[-20:]
            return '\n'.join(f'- {f}' for f in recent)
        except Exception as e:
            _logger.debug('ContextCompactor._build_artifacts_block failed: %s', e)
            return '(artifacts unavailable)'

    def _build_compaction_prompt(
        self,
        messages_to_compress: list['Message'],
        artifacts_block: str,
        n_compressed: int,
        n_next: int,
    ) -> str:
        """构建发给 LLM 的压缩 prompt。"""
        # 序列化待压缩消息（只取 text content，截断超长内容）
        msg_lines: list[str] = []
        for msg in messages_to_compress:
            role = msg.role.value
            content = msg.content or ''
            if isinstance(content, list):
                content = ' '.join(
                    b.get('text', '') for b in content
                    if isinstance(b, dict) and b.get('type') == 'text'
                )
            content_str = str(content)
            if len(content_str) > 500:
                content_str = content_str[:500] + '...(truncated)'
            msg_lines.append(f'[{role}]: {content_str}')
        history_text = '\n'.join(msg_lines)

        schema = self._COMPACT_SCHEMA.format(
            n_compressed=n_compressed,
            artifacts_block=artifacts_block,
            n_next=n_next,
        )

        return (
            f'You are a context compressor. Summarize the following conversation history '
            f'into a compact context block. You MUST follow the exact schema below.\n\n'
            f'IMPORTANT: The "## Produced Artifacts" section is pre-filled — do NOT modify '
            f'file paths. Only fill in the other sections.\n\n'
            f'SCHEMA TO FOLLOW:\n{schema}\n\n'
            f'CONVERSATION HISTORY TO COMPRESS ({n_compressed} messages):\n'
            f'{history_text}\n\n'
            f'Now produce the compact context block following the schema exactly.'
        )


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
        # C.4 — ContextCompactor 实例（由外部注入，默认 None）
        self._compactor: ContextCompactor | None = None

    def set_token_counter(self, counter: 'TokenCounter') -> None:
        """设置 token 计数器"""
        self._token_counter = counter

    # C.4 — set_compactor / should_compact
    def set_compactor(self, compactor: ContextCompactor) -> None:
        """注入 ContextCompactor 实例（由 MatMasterAgent.__init__ 调用）。"""
        self._compactor = compactor

    def should_compact(self, dialog: Dialog) -> bool:
        """判断是否需要 compact（比 should_truncate 更早触发，仅按 token 数判断）。"""
        cfg = self.config.compaction
        if not cfg.enabled:
            return False
        return self.estimate_tokens(dialog) > cfg.trigger_tokens

    def estimate_tokens(self, dialog: Dialog) -> int:
        """估算对话的 token 数

        如果设置了 token 计数器，使用计数器；否则使用简单估算。
        """
        if self._token_counter:
            return self._token_counter.count_dialog(dialog)

        # 保守估算：约3个字符 = 1 token（对中英文混合内容更准确）
        # Bug fix: 计算所有消息的完整字符数（包括 tool_calls 参数）
        total_chars = sum(
            self._message_char_len(msg) for msg in dialog.messages
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

    @staticmethod
    def _message_char_len(msg: 'Message') -> int:
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
                        total += len(args if isinstance(args, str) else json.dumps(args))
        return total

    @staticmethod
    def _tools_char_len(tools: list | None) -> int:
        """计算工具定义的字符数（每次 API 调用都会随 tools 参数发送）。"""
        import json
        if not tools:
            return 0
        try:
            return len(json.dumps([t.model_dump() if hasattr(t, 'model_dump') else t for t in tools]))
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

    @staticmethod
    def _safe_tail(
        other_messages: list['Message'],
        n_turns: int,
    ) -> list['Message']:
        """从 other_messages（非 system 消息列表）的尾部取 n_turns 个完整 tool-transaction。

        一个 tool-transaction 定义为：
          1 个 AssistantMessage（可能带 tool_calls）+ 其后紧跟的所有 ToolMessage（0 个或多个）

        规则：
        - 从尾部向前扫描，以 AssistantMessage 为 transaction 起点。
        - 每找到一个完整 transaction（assistant + 其所有 tool results），计数 +1。
        - 收集到 n_turns 个 transaction 后停止，返回这段消息。
        - 若 other_messages 中 assistant 消息不足 n_turns 个，返回全部 other_messages。
        - 保证返回的片段：
            * 不以孤立 ToolMessage 开头
            * 每个 AssistantMessage 的所有 tool_call_id 都有对应 ToolMessage
        """
        if not other_messages:
            return []

        # 从尾部向前找 transaction 边界
        # 先把消息按 transaction 分组（从前往后）
        transactions: list[list['Message']] = []
        i = 0
        while i < len(other_messages):
            msg = other_messages[i]
            if msg.role.value == 'assistant':
                # 收集这个 assistant 及其后续所有 tool 消息
                tx: list['Message'] = [msg]
                j = i + 1
                while j < len(other_messages) and other_messages[j].role.value == 'tool':
                    tx.append(other_messages[j])
                    j += 1
                transactions.append(tx)
                i = j
            else:
                # user 消息或其他非 assistant/tool 消息：单独作为一个 transaction
                transactions.append([msg])
                i += 1

        # 取最后 n_turns 个 transaction
        tail_transactions = transactions[-n_turns:] if len(transactions) >= n_turns else transactions
        result: list['Message'] = []
        for tx in tail_transactions:
            result.extend(tx)
        return result

    def _truncate_latest_half(self, dialog: Dialog) -> Dialog:
        """保留最新一半的历史（按完整 tool-transaction 为单位截断）。

        保留 system 消息 + 最近一半的 transaction 数量。
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

        # 统计 transaction 数量（以 assistant 消息为单位）
        n_transactions = sum(1 for m in other_msgs if m.role.value == 'assistant')
        if n_transactions <= 1:
            return dialog

        keep_turns = max(1, n_transactions // 2)
        tail = self._safe_tail(other_msgs, keep_turns)

        if len(tail) >= len(other_msgs):
            return dialog

        new_messages = system_msgs + tail
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

        tail = self._safe_tail(other_messages, preserve_turns)
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
        # C.5 — 真实实现（替换 stub）
        cfg = self.config.compaction
        if not cfg.enabled or self._compactor is None:
            return self._truncate_latest_half(dialog)
        try:
            return self._compactor.compact(dialog)
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
        """
        # C.6 — compact 优先于 truncate
        max_iterations = 10
        for _ in range(max_iterations):
            # compact 优先（token 阈值更低，在 truncate 之前触发）
            if self.should_compact(dialog):
                compacted = self._truncate_with_summary(dialog)
                if len(compacted.messages) < len(dialog.messages):
                    dialog = compacted
                    continue
            if not self.should_truncate(dialog):
                break
            truncated = self.truncate(dialog)
            # 如果截断后消息数没有减少，说明无法继续截断，退出
            if len(truncated.messages) >= len(dialog.messages):
                break
            dialog = truncated
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
