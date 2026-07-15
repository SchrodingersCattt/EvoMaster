"""Feishu alerts for critical tool timeout observations."""

from __future__ import annotations

import logging

from matmaster.types.runtime_ports import ToolTimeoutNotice
from src.dao.redis_dao import get_redis_dao
from src.utils.constant import SERVICE_ENV
from src.utils.feishu_notifier import CARD_TEMPLATE_RED, notify_post_async
from src.utils.worker_id import get_worker_id

logger = logging.getLogger(__name__)

_ALERT_TTL_SEC = 300
_ALERT_KEY_PREFIX = "matmaster:tool_timeout_alert:"
_CRITICAL_TOOL_PREFIXES = ("mat_struct_db_",)


def _is_critical_tool(tool_name: str) -> bool:
    name = (tool_name or "").strip()
    return any(name.startswith(prefix) for prefix in _CRITICAL_TOOL_PREFIXES)


def _session_url(session_id: str) -> str:
    env = (SERVICE_ENV or "").strip().lower()
    host = "matmaster" if not env or env == "prod" else f"matmaster.{env}"
    return f"https://{host}.bohrium.com/matmaster/chat-evo/{session_id}"


def _truncate(value: str, max_chars: int) -> str:
    text = (value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...<truncated>"


def _should_send_alert(notice: ToolTimeoutNotice) -> bool:
    key = (
        f"{_ALERT_KEY_PREFIX}{notice.tool_name}:"
        f"{notice.session_id}:{notice.spawn_id or 'root'}"
    )
    reserved = get_redis_dao().try_reserve_nx(key, "1", _ALERT_TTL_SEC)
    if reserved is False:
        return False
    if reserved is None:
        logger.warning(
            "tool timeout alert dedup unavailable, sending alert without reservation "
            "session_id=%s task_id=%s tool=%s",
            notice.session_id,
            notice.task_id,
            notice.tool_name,
        )
    return True


class FeishuToolTimeoutObserver:
    """Send rate-limited Feishu cards for critical tool timeouts."""

    def __call__(self, notice: ToolTimeoutNotice) -> None:
        if not _is_critical_tool(notice.tool_name):
            return
        if not _should_send_alert(notice):
            return

        rows = [
            ("会话ID", notice.session_id),
            ("会话地址", _session_url(notice.session_id)),
            ("任务ID", notice.task_id or "-"),
            ("子任务", notice.spawn_id or "root"),
            ("工具", notice.tool_name),
            ("Tool Call ID", notice.tool_call_id),
            ("轮次", str(notice.turn)),
            ("执行节点", get_worker_id()),
            ("错误", _truncate(notice.result_content, 500) or "-"),
            ("参数摘要", _truncate(notice.arguments_preview, 1000) or "-"),
        ]
        notify_post_async("核心 MCP 工具超时", rows, template=CARD_TEMPLATE_RED)
