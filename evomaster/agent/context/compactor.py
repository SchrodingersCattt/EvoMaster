"""LLM 驱动的对话历史压缩。

ContextCompactor 实现见本模块。
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Callable

from evomaster.utils.types import AssistantMessage, Dialog, Message

from .config import CompactionConfig
from .truncation import safe_tail

if TYPE_CHECKING:
    from .manager import ContextManager

_logger = logging.getLogger(__name__)


class CompactionError(Exception):
    """Raised when ContextCompactor.compact() fails."""


class ContextCompactor:
    """LLM 驱动的对话历史压缩器。

    compact() 方法：
    1. 从 dialog 中提取 system 消息（保留）+ 最近 preserve_recent_turns 轮（保留）
    2. 把中间历史发给 LLM 生成摘要；execution_journal 摘要作为权威事实一并注入
    3. 摘要格式强制包含：
       - ## Summary of Prior Work
       - ## Produced Artifacts（文件路径列表，从 execution_journal 注入，防幻觉）
       - ## Key Findings
       - ## Current Status
    4. 返回新 Dialog：[system messages] + [COMPACT CONTEXT system msg] + [recent turns]

    压缩前会检查 compressible tokens 占比，不足时直接返回原 dialog。
    _build_compaction_prompt() 按角色分级截断消息内容，保护 Markdown 表格 / JSON。
    preserve_recent_turns 在 token 压力极大时自动缩减。
    """

    # Compact message 格式模板（注入到 prompt 中强制 LLM 遵守）
    _COMPACT_SCHEMA = """\
[COMPACT CONTEXT — Turns 1-{n_turns}]

## Summary of Prior Work
{{2-3 sentences summarizing completed work}}

## Produced Artifacts
{artifacts_block}

## Key Findings
- {{key finding, ≤80 chars each}}

## Current Status
Turn {n_turns} completed. Next: Turn {n_next} — {{next_goal}}

## Errors / Warnings
{{list failed steps with reasons; omit section if none}}"""

    # 按角色分级的截断上限（字符数）——压缩 prompt 中用于控制单条消息长度
    _ROLE_CHAR_LIMITS: dict[str, int] = {
        'user': 8000,  # 用户消息可能含数据表格，放宽
        'assistant': 2000,  # assistant 思考/规划文本
        'tool': 1500,  # tool observation 通常可截断
        'system': 500,  # system 消息在压缩 prompt 里不需要完整
    }

    def __init__(
        self,
        config: CompactionConfig,
        llm_caller: Callable[[Dialog], AssistantMessage],
        execution_journal: object | None = None,
    ) -> None:
        self._config = config
        self._llm_caller = llm_caller
        self._execution_journal = execution_journal

    def compact(
        self,
        dialog: Dialog,
        context_manager: ContextManager | None = None,
        on_event: Callable[[dict], None] | None = None,
    ) -> Dialog:
        """压缩对话历史，返回新 Dialog。失败时抛出 CompactionError。

        保留尾部使用 safe_tail() 以完整 tool-transaction 为单位，
        确保保留段不含孤立 ToolMessage 或不完整的 assistant/tool 配对。

        若 compressible tokens 占比低于 min_compressible_ratio，直接返回原 dialog。
        token 压力极大时自动缩减 preserve_recent_turns 到 1。
        on_event 回调在 started/skipped/finished/failed 时触发。
        """
        from evomaster.utils.types import SystemMessage, UserMessage

        def _emit(status: str, **extra: object) -> None:
            if on_event is None:
                return
            payload: dict = {'type': 'context_compaction', 'status': status}
            payload.update(extra)
            try:
                on_event(payload)
            except Exception:
                pass

        messages = dialog.messages
        preserve_turns = self._config.preserve_recent_turns

        # 1. 分离 system 消息和非 system 消息
        #    Strip prior [COMPACT CONTEXT] system messages to prevent accumulation
        #    across multiple compaction cycles within the same run.
        system_msgs: list[Message] = []
        other_msgs: list[Message] = []
        for msg in messages:
            if msg.role.value == 'system':
                content = getattr(msg, 'content', '') or ''
                if isinstance(content, str) and content.startswith('[COMPACT CONTEXT]'):
                    _logger.debug(
                        'ContextCompactor.compact: stripping prior [COMPACT CONTEXT] '
                        'system message (%d chars) to prevent accumulation',
                        len(content),
                    )
                    continue  # drop old compact context — will be replaced by new one
                system_msgs.append(msg)
            else:
                other_msgs.append(msg)

        # Pin the first user message (the original task query) — never compress it.
        # It is always other_msgs[0] (the task description sent by the user/agent).
        # After compact the layout is: system_msgs + [first_user_msg] + [COMPACT CONTEXT] + recent_msgs
        pinned_first_user: list[Message] = []
        if other_msgs and other_msgs[0].role.value == 'user':
            pinned_first_user = [other_msgs[0]]
            other_msgs = other_msgs[1:]

        # 记录 compact 前 token 数
        tokens_before = (
            context_manager.estimate_tokens(dialog) if context_manager else None
        )

        # 检查 compressible tokens 占比，避免无效压缩
        # fixed = system + pinned_first_user + tools（这些永远不会被压缩）
        if context_manager is not None:
            total_tokens = context_manager.estimate_tokens(dialog)
            system_tokens = (
                sum(context_manager._message_char_len(m) for m in system_msgs) // 3
            )
            pinned_tokens = (
                sum(context_manager._message_char_len(m) for m in pinned_first_user)
                // 3
            )
            tools_tokens = context_manager._tools_char_len(dialog.tools) // 3
            fixed_tokens = system_tokens + pinned_tokens + tools_tokens
            compressible_tokens = total_tokens - fixed_tokens
            min_ratio = self._config.min_compressible_ratio
            if total_tokens > 0 and (compressible_tokens / total_tokens) < min_ratio:
                _logger.warning(
                    'ContextCompactor.compact: skipping — compressible tokens (%d) / total (%d) = %.1f%% '
                    '< min_compressible_ratio=%.0f%%. system+tools dominate; compact would be ineffective.',
                    compressible_tokens,
                    total_tokens,
                    100.0 * compressible_tokens / total_tokens,
                    100.0 * min_ratio,
                )
                _emit(
                    'skipped',
                    reason='min_compressible_ratio',
                    tokens_before=total_tokens,
                    compressible_ratio=round(compressible_tokens / total_tokens, 4),
                )
                return dialog

        # 动态调整 preserve_recent_turns
        # 若 token 压力极大（超过 trigger 的 1.2 倍），自动缩减到 1 以最大化可压缩空间
        effective_trigger = self._config.effective_trigger_tokens()
        if context_manager is not None and tokens_before is not None:
            if tokens_before > effective_trigger * 1.2 and preserve_turns > 1:
                preserve_turns = 1
                _logger.debug(
                    'ContextCompactor.compact: high token pressure (%d > %.0f), '
                    'reducing preserve_recent_turns to 1',
                    tokens_before,
                    effective_trigger * 1.2,
                )

        # 2. 用 safe_tail 取最近 preserve_turns 个完整 tool-transaction 作为保留尾部
        recent_msgs = safe_tail(other_msgs, preserve_turns)
        if len(recent_msgs) >= len(other_msgs):
            # 没有足够消息可压缩（pinned_first_user 已被移出，other_msgs 全部在 recent），直接返回原 dialog
            return dialog

        msgs_to_compress = other_msgs[: len(other_msgs) - len(recent_msgs)]

        # 3. 构建 artifacts block（防幻觉：由 Python 注入，LLM 只能引用）
        artifacts_block = self._build_artifacts_block()

        # 构建结构化事实块（表格/约束/open tasks/tool 输出关键值）
        # 同时扫描 pinned_first_user（原始任务 query），确保任务里的表格/约束也被提取
        structured_facts_block = self._build_structured_facts_block(
            pinned_first_user + msgs_to_compress
        )

        # 从 execution_journal 提取结构化执行摘要，注入 compaction prompt 作为权威事实。
        # compaction LLM 能看到"哪些工具被调用了、哪些没有"，
        # 无法在 ## Summary of Prior Work 中声称"已完成"从未执行的步骤。
        journal_summary = ''
        if self._execution_journal is not None:
            try:
                journal_summary = self._execution_journal.get_compact_summary(
                    include_details=True
                )
            except Exception:
                pass

        # 统计 assistant transaction 数（而非消息条数）作为"轮次"
        n_turns = sum(1 for m in msgs_to_compress if m.role.value == 'assistant')
        if n_turns == 0:
            n_turns = len(msgs_to_compress)  # fallback：无 assistant 消息时用消息数
        n_next = n_turns + 1

        # 4. 构建压缩 prompt
        compaction_prompt = self._build_compaction_prompt(
            msgs_to_compress,
            artifacts_block,
            structured_facts_block,
            n_turns,
            n_next,
            journal_summary=journal_summary,
        )

        # 5. 调用 LLM 生成摘要
        _emit(
            'started',
            tokens_before=tokens_before,
            trigger_tokens=self._config.effective_trigger_tokens(),
        )
        _t0 = time.monotonic()
        try:
            compaction_dialog = type(dialog)(
                messages=system_msgs + [UserMessage(content=compaction_prompt)],
                tools=[],
            )
            reply = self._llm_caller(compaction_dialog)
            compact_text = (reply.content or '').strip()
            if not compact_text:
                raise CompactionError('LLM returned empty compact message')
        except CompactionError as e:
            _emit('failed', reason=str(e), tokens_before=tokens_before)
            raise
        except Exception as e:
            _emit('failed', reason=str(e), tokens_before=tokens_before)
            raise CompactionError(f'LLM call failed: {e}') from e
        _duration_ms = int((time.monotonic() - _t0) * 1000)

        # 6. 截断 compact_text 到 max_compact_tokens（按字符估算：1 token ≈ 3 chars）
        max_chars = self._config.max_compact_tokens * 3
        if len(compact_text) > max_chars:
            compact_text = compact_text[:max_chars] + '\n...(compact message truncated)'

        # 7. 构建新 Dialog：system msgs + pinned first user msg + COMPACT CONTEXT system msg + recent turns
        # pinned_first_user 保留原始任务描述，确保 LLM 始终能看到完整的原始 query
        compact_sys_msg = SystemMessage(content=f'[COMPACT CONTEXT]\n\n{compact_text}')
        new_messages = system_msgs + pinned_first_user + [compact_sys_msg] + recent_msgs

        # 记录 compact 前后 token 数
        if context_manager is not None:
            new_dialog_tmp = type(dialog)(messages=new_messages, tools=dialog.tools)
            tokens_after = context_manager.estimate_tokens(new_dialog_tmp)
            _logger.info(
                'ContextCompactor.compact: %d turns compressed → compact_msg (%d chars) + %d recent msgs | '
                'tokens: %d → %d (saved %d, %.1f%%)',
                n_turns,
                len(compact_text),
                len(recent_msgs),
                tokens_before,
                tokens_after,
                tokens_before - tokens_after,
                (
                    100.0 * (tokens_before - tokens_after) / tokens_before
                    if tokens_before
                    else 0
                ),
            )
            _emit(
                'finished',
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                tokens_saved=tokens_before - tokens_after,
                saved_ratio=(
                    round((tokens_before - tokens_after) / tokens_before, 4)
                    if tokens_before
                    else 0.0
                ),
                compressed_turns=n_turns,
                recent_msgs_kept=len(recent_msgs),
                duration_ms=_duration_ms,
            )
        else:
            _logger.debug(
                'ContextCompactor.compact: %d turns compressed → compact_msg (%d chars) + %d recent msgs',
                n_turns,
                len(compact_text),
                len(recent_msgs),
            )
            _emit(
                'finished',
                tokens_before=tokens_before,
                compressed_turns=n_turns,
                recent_msgs_kept=len(recent_msgs),
                duration_ms=_duration_ms,
            )

        return type(dialog)(
            messages=new_messages,
            tools=dialog.tools,
            meta={**dialog.meta, 'compacted': True, 'compressed_turns': n_turns},
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

    def _build_structured_facts_block(self, msgs_to_compress: list[Message]) -> str:
        """从待压缩消息中提取结构化事实，直接注入 compact prompt（防幻觉）。

        提取来源：
        - user 消息：Markdown 表格、约束条件、未完成任务标记
        - tool 消息：数值键值对、DOI、文件路径、空输出检测
          空输出检测是关键：若工具返回空内容，记录 [EMPTY OUTPUT]，
          compaction LLM 无法声称该工具"已成功提取数据"。
        """
        import json as _json

        tables: list[str] = []
        constraints: list[str] = []
        open_tasks: list[str] = []
        # tool 消息提取的键值对（数值/DOI/路径/空输出）
        tool_key_values: list[str] = []

        # 预编译正则（tool 消息提取用）
        _doi_re = re.compile(r'10\.\d{4,}/\S+')
        _path_re = re.compile(
            r'(?:^|[\s"\'])(/[\w./\-_]+\.[\w]+|\.\/[\w./\-_]+\.[\w]+)'
        )
        _numeric_key_re = re.compile(
            r'"([\w_]*(Ps|Pr|Tc|value|result|score|energy|bandgap|'
            r'polarization|coercive|temperature|conductivity|permittivity'
            r'|dielectric|piezo|ferro|pyro|coupling|strain|stress|modulus'
            r'|density|formula|material|compound|doi|title|author)[^"]*)"'
            r'\s*:\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|"[^"]{1,120}")',
            re.IGNORECASE,
        )

        constraint_keywords = re.compile(
            r'\b(constraint|bound|limit|range|must|should not|≤|≥|<|>|at\.%|wt\.%|ppm)',
            re.IGNORECASE,
        )
        open_task_keywords = re.compile(
            r'\b(TODO|pending|open|step\s+\d+|task\s+\d+|next[:\s])',
            re.IGNORECASE,
        )

        for msg in msgs_to_compress:
            role = msg.role.value
            content = msg.content or ''
            if isinstance(content, list):
                content = ' '.join(
                    b.get('text', '')
                    for b in content
                    if isinstance(b, dict) and b.get('type') == 'text'
                )
            content_str = str(content)

            # ── user 消息：原有提取逻辑 ──────────────────────────────────────
            if role == 'user':
                # 提取 Markdown 表格（连续的 | 行）
                table_lines: list[str] = []
                in_table = False
                for line in content_str.splitlines():
                    stripped = line.strip()
                    if stripped.startswith('|') and stripped.endswith('|'):
                        table_lines.append(line)
                        in_table = True
                    else:
                        if in_table and table_lines:
                            tables.append('\n'.join(table_lines))
                            table_lines = []
                        in_table = False
                if in_table and table_lines:
                    tables.append('\n'.join(table_lines))

                # 提取约束条件行
                for line in content_str.splitlines():
                    if constraint_keywords.search(line) and len(line.strip()) > 10:
                        constraints.append(line.strip())

                # 提取未完成任务
                for line in content_str.splitlines():
                    if open_task_keywords.search(line) and len(line.strip()) > 10:
                        open_tasks.append(line.strip())

            # ── tool 消息（Fix A）：提取数值事实 + 空输出检测 ─────────────────
            elif role == 'tool':
                # 空输出检测：observation 为空字符串，或 JSON 中 observation 字段为空
                stripped = content_str.strip()
                is_empty = False
                if not stripped:
                    is_empty = True
                else:
                    try:
                        parsed = _json.loads(stripped)
                        obs = parsed.get('observation', _SENTINEL := object())
                        if obs is not _SENTINEL and (obs == '' or obs is None):
                            is_empty = True
                    except Exception:
                        pass

                # 尝试从 tool_call_id 或消息元数据中获取工具名（不一定有，降级为 'tool'）
                tool_label = getattr(msg, 'tool_call_id', None) or 'tool'

                if is_empty:
                    tool_key_values.append(
                        f'[{tool_label}]: [EMPTY OUTPUT — tool returned no data; '
                        f'do NOT claim results from this call exist]'
                    )
                else:
                    # 提取数值键值对（最多 10 个，避免 prompt 膨胀）
                    kv_matches = _numeric_key_re.findall(content_str)
                    if kv_matches:
                        # findall returns (full_key, subword_match, value) — use full_key and value
                        kv_items = [f'{k}={val}' for k, _sub, val in kv_matches[:10]]
                        tool_key_values.append(
                            f'[{tool_label}] key values: ' + ', '.join(kv_items)
                        )

                    # 提取 DOI
                    dois = _doi_re.findall(content_str)
                    if dois:
                        tool_key_values.append(
                            f'[{tool_label}] DOIs: '
                            + ', '.join(dict.fromkeys(dois[:5]))
                        )

                    # 提取文件路径
                    paths = _path_re.findall(content_str)
                    if paths:
                        tool_key_values.append(
                            f'[{tool_label}] paths: '
                            + ', '.join(dict.fromkeys(paths[:5]))
                        )

        parts: list[str] = []

        if tables:
            # 最多保留前 3 张表（避免 prompt 过长）
            parts.append(
                '## Key Data Tables (extracted, do NOT modify)\n'
                + '\n\n'.join(tables[:3])
            )

        if constraints:
            # 去重，最多 20 条
            seen: set[str] = set()
            unique_constraints = []
            for c in constraints:
                if c not in seen:
                    seen.add(c)
                    unique_constraints.append(c)
            parts.append(
                '## Constraints / Bounds (extracted, do NOT modify)\n'
                + '\n'.join(f'- {c}' for c in unique_constraints[:20])
            )

        if open_tasks:
            seen_tasks: set[str] = set()
            unique_tasks = []
            for t in open_tasks:
                if t not in seen_tasks:
                    seen_tasks.add(t)
                    unique_tasks.append(t)
            parts.append(
                '## Open Tasks (extracted, do NOT modify)\n'
                + '\n'.join(f'- {t}' for t in unique_tasks[:10])
            )

        # tool 消息提取的键值（含空输出标记）
        if tool_key_values:
            seen_kv: set[str] = set()
            unique_kv = []
            for kv in tool_key_values:
                if kv not in seen_kv:
                    seen_kv.add(kv)
                    unique_kv.append(kv)
            parts.append(
                '## Tool Output Key Values (Python-extracted, do NOT modify or hallucinate)\n'
                + '\n'.join(f'- {kv}' for kv in unique_kv[:30])
            )

        if not parts:
            return ''
        return '\n\n'.join(parts)

    def _truncate_message_content(self, role: str, content_str: str) -> str:
        """按角色分级截断消息内容，保护 Markdown 表格 / JSON / 代码块。

        截断规则：
        1. 优先保留 Markdown 表格（完整行）
        2. 优先保留 JSON 对象的前 N 字符（不在中间截断）
        3. 按角色设定不同上限
        """
        limit = self._ROLE_CHAR_LIMITS.get(role, 1500)

        if len(content_str) <= limit:
            return content_str

        # 检测是否含 Markdown 表格：若含表格，尝试保留完整表格 + 截断其余
        lines = content_str.splitlines()
        table_end_idx = -1
        in_table = False
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('|') and stripped.endswith('|'):
                in_table = True
                table_end_idx = idx
            elif in_table:
                # 表格结束
                break

        if table_end_idx >= 0:
            # 保留到表格结束行，再加上剩余内容（截断到 limit）
            table_text = '\n'.join(lines[: table_end_idx + 1])
            remaining = '\n'.join(lines[table_end_idx + 1 :])
            remaining_limit = max(0, limit - len(table_text) - 1)
            if remaining_limit > 0 and remaining:
                return (
                    table_text + '\n' + remaining[:remaining_limit] + '...(truncated)'
                )
            return table_text

        # 无表格：直接按 limit 截断
        return content_str[:limit] + '...(truncated)'

    def _build_compaction_prompt(
        self,
        messages_to_compress: list[Message],
        artifacts_block: str,
        structured_facts_block: str,
        n_turns: int,
        n_next: int,
        journal_summary: str = '',
    ) -> str:
        """构建发给 LLM 的压缩 prompt。

        - 按角色分级截断消息内容（保护表格/JSON）
        - 注入结构化事实块（Key Data Tables / Constraints / Open Tasks / Tool Output Key Values）
        - 注入 execution_journal 结构化摘要作为权威事实（LLM 不得与之矛盾）
        """
        # 序列化待压缩消息（按角色分级截断）
        msg_lines: list[str] = []
        for msg in messages_to_compress:
            role = msg.role.value
            content = msg.content or ''
            if isinstance(content, list):
                content = ' '.join(
                    b.get('text', '')
                    for b in content
                    if isinstance(b, dict) and b.get('type') == 'text'
                )
            content_str = str(content)
            # 按角色分级截断，保护表格
            content_str = self._truncate_message_content(role, content_str)
            msg_lines.append(f'[{role}]: {content_str}')
        history_text = '\n'.join(msg_lines)

        schema = self._COMPACT_SCHEMA.format(
            n_turns=n_turns,
            artifacts_block=artifacts_block,
            n_next=n_next,
        )

        # 若有结构化事实块，附加到 prompt
        structured_section = ''
        if structured_facts_block:
            structured_section = (
                f'\nPRE-EXTRACTED STRUCTURED FACTS (Python-injected, do NOT hallucinate or modify):\n'
                f'{structured_facts_block}\n'
            )

        # 注入 execution_journal 摘要作为权威事实
        # LLM 必须以此为准，不得在摘要中声称执行了 journal 中不存在的步骤
        journal_section = ''
        if journal_summary:
            journal_section = (
                f'\nEXECUTION JOURNAL (Python-injected, authoritative — do NOT contradict):\n'
                f'{journal_summary}\n'
                f'IMPORTANT: Your ## Summary of Prior Work and ## Key Findings MUST be consistent '
                f'with the above journal. If a tool does not appear in the journal, it was NEVER '
                f'called — do NOT claim its results exist.\n'
            )

        return (
            f'You are a context compressor. Summarize the following conversation history '
            f'into a compact context block. You MUST follow the exact schema below.\n\n'
            f'IMPORTANT: The "## Produced Artifacts" section is pre-filled — do NOT modify '
            f'file paths. Only fill in the other sections.\n\n'
            f'SCHEMA TO FOLLOW:\n{schema}\n'
            f'{structured_section}'
            f'{journal_section}\n'
            f'CONVERSATION HISTORY TO COMPRESS ({n_turns} turns, {len(messages_to_compress)} messages):\n'
            f'{history_text}\n\n'
            f'Now produce the compact context block following the schema exactly.'
        )
