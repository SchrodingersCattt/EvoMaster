"""MatMaster 两条 run 路径共用的轻量判断（生产 AgentRunService + 本地 Web _run_agent_sync）。"""

from __future__ import annotations

from typing import Any


def is_streaming_thought_event(event_type: str, extra: dict[str, Any]) -> bool:
    """是否为流式 thought 标记片段（start / streaming / end），不落库、通常不推前端。"""
    return event_type == 'thought' and extra.get('stream_state') in {
        'start',
        'streaming',
        'end',
    }


def should_persist_chat_event(event_type: str, extra: dict[str, Any]) -> bool:
    """仅持久化「可回放」事件：排除 log_line、llm_token、流式 thought 片段。"""
    if event_type in {'log_line', 'llm_token'}:
        return False
    return not is_streaming_thought_event(event_type, extra)


def should_skip_push_for_frontend(
    mode: str,
    raw_source: str,
    event_type: str,
    extra: dict[str, Any],
) -> bool:
    """是否不向浏览器推送：assistant_state、Planner 流式 thought、direct 下非流式完整 thought。

    raw_source 须为 normalize 之前的 source 字符串，以便识别 ``Planner``（normalize 后会变成 MatMaster）。
    """
    if event_type == 'assistant_state':
        return True
    if raw_source == 'Planner' and is_streaming_thought_event(event_type, extra):
        return True
    return (
        mode == 'direct'
        and event_type == 'thought'
        and not is_streaming_thought_event(event_type, extra)
    )
