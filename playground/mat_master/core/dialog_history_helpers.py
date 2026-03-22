"""多轮对话事件裁剪与 MatMaster discovery TaskInstance 构造（生产 run + 本地 Web run 共用）。"""

from __future__ import annotations

from typing import Any

from evomaster.utils.types import TaskInstance


def trim_events_for_dialog_history(
    all_events: list[dict[str, Any]],
    max_events: int,
) -> list[dict[str, Any]]:
    """若最后一条为 User/query（当前轮用户输入），则从列表中去掉再参与多轮上下文；再截断为最近 ``max_events`` 条。

    与 DB 或内存中的事件结构一致：``source == 'User'`` 且 ``type == 'query'`` 表示本轮用户提问。
    """
    if not all_events:
        return []
    last = all_events[-1]
    if last.get('source') == 'User' and last.get('type') == 'query':
        trimmed = all_events[:-1]
    else:
        trimmed = list(all_events)
    if len(trimmed) > max_events:
        return trimmed[-max_events:]
    return trimmed


def build_mat_master_discovery_task(
    task_id: str,
    user_prompt: str,
    dialog_history: list[Any],
) -> TaskInstance:
    """构造 MatMaster discovery 任务的 ``TaskInstance``（``meta['dialog_history']``）。"""
    return TaskInstance(
        task_id=task_id,
        task_type='discovery',
        description=user_prompt,
        meta={'dialog_history': dialog_history},
    )
