"""多轮对话历史：将 DB 中的 chat 事件转换为 Agent Dialog 所需的 Message 列表（可序列化 dict）。"""

import json
from typing import Any

from evomaster.utils.types import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from src.utils.chat_event_source import normalize_event_source


class ChatHistoryConverter:
    """将 get_session_events 返回的事件列表转为 task.meta['dialog_history'] 所需的 Message 序列化列表。"""

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
                try:
                    msg = AssistantMessage.model_validate(ev.get('content') or {})
                except Exception:
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
        return out
