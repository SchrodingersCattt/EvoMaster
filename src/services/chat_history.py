"""多轮对话历史：将 DB 中的 chat 事件转换为 Agent Dialog 所需的 Message 列表（可序列化 dict）。"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

from evomaster.utils.types import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from src.utils.chat_event_source import normalize_event_source


def _summarize_assistant_state_content_for_log(raw: Any) -> str:
    """失败时单行描述 content 形态，避免整段 JSON 撑爆日志。"""
    if raw is None:
        return 'type=None'
    if isinstance(raw, str):
        s = raw.replace('\n', '\\n')
        tail = '...' if len(s) > 200 else ''
        return f'type=str len={len(raw)} preview={s[:200]!r}{tail}'
    if isinstance(raw, dict):
        keys = sorted(raw.keys())
        role = raw.get('role')
        tcs = raw.get('tool_calls')
        n_tc = len(tcs) if isinstance(tcs, list) else 'n/a'
        c = raw.get('content')
        c_kind = type(c).__name__
        meta = raw.get('meta')
        meta_keys = sorted(meta.keys()) if isinstance(meta, dict) else []
        mk = meta_keys[:12]
        extra = '...' if len(meta_keys) > 12 else ''
        return (
            f'type=dict keys={keys} role={role!r} tool_calls_count={n_tc} '
            f'content_type={c_kind} meta_keys={mk}{extra}'
        )
    if isinstance(raw, list):
        return f'type=list len={len(raw)}'
    return f'type={type(raw).__name__} repr={repr(raw)[:300]}'


def _serialized_message_role(m: dict) -> str:
    """Normalize role from model_dump() (str or MessageRole) for comparisons."""
    r = m.get('role')
    if isinstance(r, str):
        return r.strip().lower()
    if r is not None and hasattr(r, 'value'):
        return str(getattr(r, 'value', r)).strip().lower()
    return str(r or '').strip().lower()


class ChatHistoryConverter:
    """将 get_session_events 返回的事件列表转为 task.meta['dialog_history'] 所需的 Message 序列化列表。"""

    @staticmethod
    def summarize_dialog_messages_for_log(messages: list[dict]) -> str:
        """将序列化后的多轮消息压成一行，便于排查 tool_use / tool_result 是否与 Bedrock 报错对齐。"""
        parts: list[str] = []
        for i, m in enumerate(messages):
            role = m.get('role', '?')
            if role == 'assistant':
                tcs = m.get('tool_calls') or []
                ids: list[str] = []
                for tc in tcs:
                    fn = (tc or {}).get('function') or {}
                    tid = (tc or {}).get('id') or ''
                    ids.append(f"{fn.get('name', '?')}:{tid[:24]}")
                parts.append(f"{i}:A(tc={len(tcs)} {','.join(ids) or '-'})")
            elif role == 'tool':
                parts.append(f"{i}:T(id={str(m.get('tool_call_id', ''))[:32]})")
            else:
                c = m.get('content')
                ln = len(str(c)) if c is not None else 0
                parts.append(f"{i}:{role}(len={ln})")
        return ' | '.join(parts)

    @staticmethod
    def validate_dialog_messages_for_llm(
        messages: list[dict],
        *,
        context: str = '',
        session_id: str | None = None,
        task_id: str | None = None,
        raw_event_count: int | None = None,
        raw_tool_call_events: int | None = None,
        raw_tool_result_events: int | None = None,
    ) -> None:
        """校验 OpenAI 格式消息是否满足 tool 配对，便于发现 Bedrock 类报错根因。

        检测：(1) 无 tool_calls 的 assistant 后紧跟 tool；(2) assistant 声明的 tool_calls
        数量与紧随其后的连续 tool 条数不一致。
        """
        extra = ''
        if session_id:
            extra += f' session_id={session_id}'
        if task_id:
            extra += f' task_id={task_id}'
        if raw_event_count is not None:
            extra += f' raw_events={raw_event_count}'
        if raw_tool_call_events is not None and raw_tool_result_events is not None:
            extra += (
                f' event_tool_calls={raw_tool_call_events}'
                f' event_tool_results={raw_tool_result_events}'
            )
            if raw_tool_call_events != raw_tool_result_events:
                logger.warning(
                    'chat_history: tool_call/tool_result event count mismatch'
                    '%s (investigate duplicate or missing events)',
                    extra,
                )

        i = 0
        while i < len(messages):
            m = messages[i]
            role = _serialized_message_role(m)
            if role != 'assistant':
                i += 1
                continue
            tcs = m.get('tool_calls') or []
            n = len(tcs)
            if n == 0:
                if i + 1 < len(messages):
                    nxt = messages[i + 1]
                    if _serialized_message_role(nxt) == 'tool':
                        logger.warning(
                            'chat_history: orphan tool_message after assistant without '
                            'tool_calls idx=%d next_tool_call_id=%s context=%s%s',
                            i,
                            str(nxt.get('tool_call_id', ''))[:64],
                            context,
                            extra,
                        )
                i += 1
                continue
            for k in range(n):
                pos = i + 1 + k
                if pos >= len(messages):
                    logger.warning(
                        'chat_history: missing tool_message(s) after assistant with '
                        'tool_calls idx=%d expected=%d got=%d context=%s%s',
                        i,
                        n,
                        k,
                        context,
                        extra,
                    )
                    break
                nxt = messages[pos]
                if _serialized_message_role(nxt) != 'tool':
                    logger.warning(
                        'chat_history: expected consecutive tool_message at idx=%d '
                        'but got role=%s (assistant tool_calls at idx=%d expected=%d) '
                        'context=%s%s',
                        pos,
                        _serialized_message_role(nxt),
                        i,
                        n,
                        context,
                        extra,
                    )
                    break
            i += 1

    @staticmethod
    def _user_content(ev: dict) -> str:
        """从 User/query 事件中取出纯文本 content。"""
        c = ev.get('content')
        if isinstance(c, str):
            return c
        if isinstance(c, dict) and 'content' in c:
            return str(c.get('content') or '')
        return str(c) if c is not None else ''

    @staticmethod
    def _assistant_content(ev: dict) -> str:
        """从 thought/finish 等事件中取出文本。"""
        c = ev.get('content')
        if isinstance(c, str):
            return c
        if isinstance(c, dict) and 'content' in c:
            return str(c.get('content') or '')
        return str(c) if c is not None else ''

    @staticmethod
    def _tool_call_from_event(ev: dict) -> dict | None:
        """从 type=tool_call 的事件 content 构建 ToolCall 的序列化 dict。"""
        c = ev.get('content')
        if not isinstance(c, dict):
            return None
        call_id = c.get('id') or ''
        name = c.get('name') or ''
        args = c.get('args')
        if isinstance(args, dict):
            args_str = json.dumps(args, ensure_ascii=False)
        elif isinstance(args, str):
            args_str = args
        else:
            args_str = json.dumps(args) if args is not None else '{}'
        return {
            'id': call_id,
            'type': 'function',
            'function': {'name': name, 'arguments': args_str},
        }

    @staticmethod
    def _tool_result_from_event(ev: dict) -> tuple[str, str, Any] | None:
        """从 type=tool_result 的事件 content 得到 (tool_call_id, name, content)。"""
        c = ev.get('content')
        if not isinstance(c, dict):
            return None
        call_id = str(c.get('id') or '')
        name = str(c.get('name') or '')
        result = c.get('result')
        if result is None:
            result = {}
        return (call_id, name, result)

    @classmethod
    def events_to_dialog_messages(cls, events: list[dict]) -> list[dict]:
        """
        将 get_session_events 返回的事件列表转为可放入 task.meta['dialog_history'] 的
        Message 序列化列表（list[dict]）。不含 SystemMessage；当前轮 User 由调用方单独传入。

        事件类型映射：
        - User/query -> UserMessage
        - thought|planner_reply -> AssistantMessage(content)
        - tool_call -> 与后续 tool_result 配对，先输出 AssistantMessage(tool_calls)，再输出 ToolMessage
        - finish -> AssistantMessage(content)
        """
        out: list[dict] = []
        pending_tool_calls: list[dict] = []
        last_assistant_text_idx: int | None = None
        assistant_state_tool_ids: set[str] = set()

        def flush_tool_calls() -> None:
            if not pending_tool_calls:
                return
            msg = AssistantMessage(
                content='',
                tool_calls=[ToolCall.model_validate(tc) for tc in pending_tool_calls],
            )
            out.append(msg.model_dump())
            pending_tool_calls.clear()

        for ev in events:
            source = normalize_event_source(ev.get('source'))
            typ = (ev.get('type') or '').strip()

            if source == 'User' and typ == 'query':
                flush_tool_calls()
                last_assistant_text_idx = None
                assistant_state_tool_ids.clear()
                text = cls._user_content(ev)
                out.append(UserMessage(content=text).model_dump())
                continue

            if source == 'MatMaster' and typ in ('thought', 'planner_reply'):
                flush_tool_calls()
                assistant_state_tool_ids.clear()
                last_assistant_text_idx = None
                text = cls._assistant_content(ev)
                if text:
                    out.append(AssistantMessage(content=text).model_dump())
                    last_assistant_text_idx = len(out) - 1
                continue

            if source == 'MatMaster' and typ == 'assistant_state':
                flush_tool_calls()
                raw_content = ev.get('content')
                try:
                    msg = AssistantMessage.model_validate(raw_content or {})
                except Exception as e:
                    logger.warning(
                        'chat_history: assistant_state model_validate failed, event skipped '
                        '(tool_calls may be missing in dialog). task_id=%s session_id=%s '
                        'content_summary=%s err=%s: %s',
                        ev.get('task_id'),
                        ev.get('session_id'),
                        _summarize_assistant_state_content_for_log(raw_content),
                        type(e).__name__,
                        e,
                    )
                    continue
                if (
                    last_assistant_text_idx is not None
                    and last_assistant_text_idx == len(out) - 1
                ):
                    out.pop()
                out.append(msg.model_dump())
                last_assistant_text_idx = None
                assistant_state_tool_ids = {
                    tc.id for tc in (msg.tool_calls or []) if getattr(tc, 'id', None)
                }
                continue

            if typ == 'tool_call':
                tc = cls._tool_call_from_event(ev)
                if tc:
                    if tc.get('id') in assistant_state_tool_ids:
                        continue
                    pending_tool_calls.append(tc)
                continue

            if typ == 'tool_result':
                triple = cls._tool_result_from_event(ev)
                if triple:
                    flush_tool_calls()
                    call_id, name, content = triple
                    assistant_state_tool_ids.discard(call_id)
                    out.append(
                        ToolMessage(
                            tool_call_id=call_id,
                            name=name,
                            content=content,
                        ).model_dump()
                    )
                continue

            if source == 'MatMaster' and typ == 'finish':
                flush_tool_calls()
                assistant_state_tool_ids.clear()
                last_assistant_text_idx = None
                text = cls._assistant_content(ev)
                if text:
                    out.append(AssistantMessage(content=text).model_dump())
                    last_assistant_text_idx = len(out) - 1
                continue

        flush_tool_calls()

        sid: str | None = None
        tid: str | None = None
        for ev in events:
            if sid is None and ev.get('session_id'):
                sid = str(ev.get('session_id'))
            if ev.get('task_id'):
                tid = str(ev.get('task_id'))
        tc_ev = sum(1 for e in events if (e.get('type') or '').strip() == 'tool_call')
        tr_ev = sum(1 for e in events if (e.get('type') or '').strip() == 'tool_result')
        ChatHistoryConverter.validate_dialog_messages_for_llm(
            out,
            context='events_to_dialog_messages',
            session_id=sid,
            task_id=tid,
            raw_event_count=len(events),
            raw_tool_call_events=tc_ev,
            raw_tool_result_events=tr_ev,
        )
        return out
