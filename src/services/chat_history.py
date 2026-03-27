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


def _is_matmaster_source(source: str) -> bool:
    """Check if source is MatMaster or MatMaster:subtype."""
    return source == 'MatMaster' or source.startswith('MatMaster:')


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
    def exclude_spawn_events(events: list[dict]) -> list[dict]:
        """Drop sub-agent rows (spawn_id set) when building parent LLM dialog history."""
        return [ev for ev in events if ev.get('spawn_id') is None]

    @staticmethod
    def exclude_task_events(events: list[dict], task_id: str | None) -> list[dict]:
        """Drop in-flight task events when the current user turn is passed separately."""
        if not task_id:
            return list(events)
        return [ev for ev in events if ev.get("task_id") != task_id]

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
        """从 thought/response/run_result 等事件中取出文本。"""
        c = ev.get('content')
        if isinstance(c, str):
            return c
        if isinstance(c, dict) and 'content' in c:
            return str(c.get('content') or '')
        return str(c) if c is not None else ''

    @staticmethod
    def _assistant_reasoning_content(raw: Any) -> str | None:
        """从 assistant_state 序列化内容中提取 reasoning_content。"""
        if not isinstance(raw, dict):
            return None
        direct = raw.get('reasoning_content')
        if isinstance(direct, str) and direct:
            return direct
        meta = raw.get('meta')
        if isinstance(meta, dict):
            fallback = meta.get('reasoning_content')
            if isinstance(fallback, str) and fallback:
                return fallback
        return None

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

    @staticmethod
    def _repair_incomplete_tool_turns(messages: list[dict]) -> list[dict]:
        """Inject synthetic ToolMessage dicts for tool_calls missing results.

        When a worker/API is forcefully interrupted mid tool execution,
        some tool_calls may lack corresponding ToolMessages. This method
        scans the message list and inserts synthetic error ToolMessages
        so the sequence is valid for LLM consumption.
        """
        out: list[dict] = []
        i = 0
        while i < len(messages):
            m = messages[i]
            role = _serialized_message_role(m)
            if role != 'assistant' or not m.get('tool_calls'):
                out.append(m)
                i += 1
                continue

            # Collect declared tool_call IDs (preserving order, dedup)
            tc_list = m['tool_calls']
            seen_ids: set[str] = set()
            ordered_tc: list[tuple[str, str]] = []  # (id, name)
            for tc in tc_list:
                tc_id = tc.get('id', '')
                tc_name = (tc.get('function') or {}).get('name', '')
                if tc_id and tc_id not in seen_ids:
                    seen_ids.add(tc_id)
                    ordered_tc.append((tc_id, tc_name))

            # Collect existing tool messages into a map
            out.append(m)
            j = i + 1
            result_map: dict[str, dict] = {}
            while j < len(messages) and _serialized_message_role(messages[j]) == 'tool':
                existing = messages[j]
                result_map[existing.get('tool_call_id', '')] = existing
                j += 1

            # Emit tool messages in tool_calls declaration order
            for tc_id, tc_name in ordered_tc:
                if tc_id in result_map:
                    out.append(result_map[tc_id])
                else:
                    out.append(
                        ToolMessage(
                            tool_call_id=tc_id,
                            name=tc_name,
                            content=f"Tool '{tc_name}' execution was interrupted. Result is unknown.",
                        ).model_dump()
                    )

            i = j

        return out

    @classmethod
    def events_to_dialog_messages(cls, events: list[dict]) -> list[dict]:
        """
        将 get_session_events 返回的事件列表转为可放入 task.meta['dialog_history'] 的
        Message 序列化列表（list[dict]）。不含 SystemMessage；当前轮 User 由调用方单独传入。

        事件类型映射：
        - User/query -> UserMessage
        - thought|planner_reply -> 缓存为 pending_reasoning
        - tool_call -> 与后续 tool_result 配对，先输出 AssistantMessage(tool_calls)，再输出 ToolMessage
        - response -> AssistantMessage(content, reasoning_content=...)
        - run_result|finish -> AssistantMessage(content, reasoning_content=...)（仅 response 缺失时作为兼容兜底）
        """
        out: list[dict] = []
        pending_tool_calls: list[dict] = []
        pending_reasoning: str | None = None
        last_assistant_text_idx: int | None = None
        assistant_state_tool_ids: set[str] = set()
        response_seen_in_turn = False

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
                if pending_reasoning:
                    out.append(
                        AssistantMessage(
                            content='',
                            reasoning_content=pending_reasoning,
                        ).model_dump()
                    )
                    pending_reasoning = None
                flush_tool_calls()
                last_assistant_text_idx = None
                assistant_state_tool_ids.clear()
                response_seen_in_turn = False
                text = cls._user_content(ev)
                out.append(UserMessage(content=text).model_dump())
                continue

            if _is_matmaster_source(source) and typ in ('thought', 'planner_reply'):
                flush_tool_calls()
                assistant_state_tool_ids.clear()
                last_assistant_text_idx = None
                text = cls._assistant_content(ev)
                if text:
                    pending_reasoning = (pending_reasoning or '') + text
                continue

            if _is_matmaster_source(source) and typ == 'response':
                flush_tool_calls()
                assistant_state_tool_ids.clear()
                last_assistant_text_idx = None
                text = cls._assistant_content(ev)
                if text or pending_reasoning:
                    out.append(
                        AssistantMessage(
                            content=text or '',
                            reasoning_content=pending_reasoning,
                        ).model_dump()
                    )
                    last_assistant_text_idx = len(out) - 1
                    response_seen_in_turn = True
                pending_reasoning = None
                continue

            if _is_matmaster_source(source) and typ == 'assistant_state':
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
                assistant_reasoning = cls._assistant_reasoning_content(raw_content)
                if pending_reasoning and not assistant_reasoning:
                    msg = msg.model_copy(update={'reasoning_content': pending_reasoning})
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
                pending_reasoning = None
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

            if _is_matmaster_source(source) and typ in ('run_result', 'finish'):
                flush_tool_calls()
                assistant_state_tool_ids.clear()
                last_assistant_text_idx = None
                if response_seen_in_turn:
                    pending_reasoning = None
                    continue
                text = cls._assistant_content(ev)
                if text or pending_reasoning:
                    out.append(
                        AssistantMessage(
                            content=text or '',
                            reasoning_content=pending_reasoning,
                        ).model_dump()
                    )
                    last_assistant_text_idx = len(out) - 1
                pending_reasoning = None
                continue

        flush_tool_calls()
        if pending_reasoning:
            out.append(
                AssistantMessage(
                    content='',
                    reasoning_content=pending_reasoning,
                ).model_dump()
            )

        sid: str | None = None
        tid: str | None = None
        for ev in events:
            if sid is None and ev.get('session_id'):
                sid = str(ev.get('session_id'))
            if ev.get('task_id'):
                tid = str(ev.get('task_id'))
        tc_ev = sum(1 for e in events if (e.get('type') or '').strip() == 'tool_call')
        tr_ev = sum(1 for e in events if (e.get('type') or '').strip() == 'tool_result')
        cls.validate_dialog_messages_for_llm(
            out,
            context='events_to_dialog_messages',
            session_id=sid,
            task_id=tid,
            raw_event_count=len(events),
            raw_tool_call_events=tc_ev,
            raw_tool_result_events=tr_ev,
        )
        return out

    @classmethod
    def events_to_messages(cls, events: list[dict]) -> list:
        """Convert DB events to matmaster Message types.

        Reuses events_to_dialog_messages() logic, then converts each dict
        to the corresponding matmaster.types.messages Message subclass.
        """
        from matmaster.types.messages import (
            AssistantMessage as MMAssistantMessage,
            ToolCallData as MMToolCallData,
            ToolMessage as MMToolMessage,
            UserMessage as MMUserMessage,
        )

        dialog_dicts = cls.events_to_dialog_messages(events)
        messages = []
        for d in dialog_dicts:
            role = d.get("role")
            if role == "user":
                messages.append(MMUserMessage(content=d.get("content", "")))
            elif role == "assistant":
                msg_kwargs: dict = {"content": d.get("content")}
                reasoning_content = d.get("reasoning_content")
                if reasoning_content is None and isinstance(d.get("meta"), dict):
                    reasoning_content = d["meta"].get("reasoning_content")
                if reasoning_content is not None:
                    msg_kwargs["reasoning_content"] = reasoning_content
                if d.get("tool_calls"):
                    import json as _json

                    tcs = []
                    for tc in d["tool_calls"]:
                        func = tc.get("function", {})
                        args_str = func.get("arguments", "{}")
                        tcs.append(
                            MMToolCallData(
                                id=tc.get("id", ""),
                                name=func.get("name", ""),
                                arguments=(
                                    _json.loads(args_str)
                                    if isinstance(args_str, str)
                                    else args_str
                                ),
                            )
                        )
                    msg_kwargs["tool_calls"] = tcs
                messages.append(MMAssistantMessage(**msg_kwargs))
            elif role == "tool":
                messages.append(
                    MMToolMessage(
                        content=d.get("content", ""),
                        tool_call_id=d.get("tool_call_id", ""),
                        tool_name=d.get("name", ""),
                    )
                )
        return messages
