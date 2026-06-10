"""Worker 侧 delivery snapshot 与 ack（对所有 run 生效，不分 origin）。

- snapshot：run 起点（acquire 成功后、run_agent 前）查询全量 pending terminal
  rows；查询执行瞬间即本轮交付边界，run 中途新终态的行留待下轮（at-least-once）。
- confirm：run 成功收尾、release_session_run 之前，按 snapshot rows 批量
  mark_handled_by_ids——ack 范围 = agent 看到范围。
handled_at 的唯一写入点在这里；poller 与 trigger enqueued 均不得 ack。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.utils.constant import env_int

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliverySnapshot:
    """一次 run 的交付边界快照（worker 内存对象，不落表）。

    rows 持全量行、不预截断：展开几条详情由 renderer 按 detail_limit 决定。
    """

    user_id: str
    org_id: str
    session_id: str
    rows: tuple[dict[str, Any], ...]
    detail_limit: int


def snapshot(
    session_id: str,
    *,
    sessions_service: Any | None = None,
    jobs_table: Any | None = None,
) -> DeliverySnapshot | None:
    """查询全量 pending terminal rows；失败或空集返回 None（不阻断 run）。"""
    try:
        svc = sessions_service
        if svc is None:
            from src.services.sessions_service import get_sessions_service

            svc = get_sessions_service()
        row = svc.get_session(session_id)
        if not row:
            return None
        user_id = str(row.get("user_id") or "")
        org_id = str(row.get("org_id") or "")
        if not (user_id and org_id):
            return None
        table = jobs_table
        if table is None:
            from src.dao.bohrium_jobs_table import get_bohrium_jobs_table

            table = get_bohrium_jobs_table()
        rows = table.list_pending_terminal_snapshot(
            user_id=user_id, org_id=org_id, session_id=session_id
        )
        if not rows:
            return None
        return DeliverySnapshot(
            user_id=user_id,
            org_id=org_id,
            session_id=session_id,
            rows=tuple(rows),
            detail_limit=env_int("BOHRIUM_DELIVERY_DETAIL_LIMIT", 20),
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "bohrium delivery snapshot failed session_id=%s",
            session_id,
            exc_info=True,
        )
        return None


def confirm(snap: DeliverySnapshot, *, jobs_table: Any | None = None) -> int:
    """按 snapshot rows 批量 mark_handled_by_ids；异常向上抛，由调用方决定善后。"""
    table = jobs_table
    if table is None:
        from src.dao.bohrium_jobs_table import get_bohrium_jobs_table

        table = get_bohrium_jobs_table()
    return table.mark_handled_by_ids(
        user_id=snap.user_id,
        org_id=snap.org_id,
        session_id=snap.session_id,
        row_ids=tuple(int(j["id"]) for j in snap.rows),
    )
