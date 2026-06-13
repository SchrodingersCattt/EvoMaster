"""Worker 侧 delivery snapshot 与 ack（对所有 run 生效，不分 origin）。

- snapshot：run 起点（acquire 成功后、run_agent 前）解析身份并查询全量 pending
  terminal rows；查询执行瞬间即本轮交付边界（rows 可空），run 中途新终态的行
  留待下轮（at-least-once）。observed_terminal 收 run 内前台查询观察到的终态。
- confirm：run 成功收尾、release_session_run 之前，按 snapshot rows 与观察集
  并集批量 ack——ack 范围 = agent 看到范围（snapshot 行 ∪ 前台查询结果）。
handled_at 的唯一写入点在这里；poller 与 trigger enqueued 均不得 ack。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.utils.constant import env_int

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliverySnapshot:
    """一次 run 的交付边界快照 + run 内前台观察集（worker 内存对象，不落表）。

    rows 持全量行、不预截断：展开几条详情由 renderer 按 detail_limit 决定。
    observed_terminal 元素为 (sandbox, job_id)；frozen 冻结字段绑定，不妨碍
    集合自身 add。写入发生在 run 内工具执行，confirm 读取在 run 结束后，
    无时间重叠。
    """

    user_id: str
    org_id: str
    session_id: str
    rows: tuple[dict[str, Any], ...]
    detail_limit: int
    observed_terminal: set[tuple[bool, str]] = field(default_factory=set)


def snapshot(
    session_id: str,
    *,
    sessions_service: Any | None = None,
    jobs_table: Any | None = None,
) -> DeliverySnapshot | None:
    """解析身份并查询全量 pending terminal rows。

    身份不可解析（session 缺失 / org 未绑定 / 查库异常）→ None：既无法 ack 也
    无法渲染，未交付行下轮重投。rows 查询失败但身份正常 → 空 rows snapshot。
    """
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
    except Exception:  # noqa: BLE001
        logger.warning(
            "bohrium delivery identity resolve failed session_id=%s",
            session_id,
            exc_info=True,
        )
        return None
    rows: list[dict[str, Any]] = []
    try:
        table = jobs_table
        if table is None:
            from src.dao.bohrium_jobs_table import get_bohrium_jobs_table

            table = get_bohrium_jobs_table()
        rows = table.list_pending_terminal_snapshot(
            user_id=user_id, org_id=org_id, session_id=session_id
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "bohrium delivery snapshot failed session_id=%s",
            session_id,
            exc_info=True,
        )
    return DeliverySnapshot(
        user_id=user_id,
        org_id=org_id,
        session_id=session_id,
        rows=tuple(rows),
        detail_limit=env_int("BOHRIUM_DELIVERY_DETAIL_LIMIT", 20),
    )


def confirm(snap: DeliverySnapshot, *, jobs_table: Any | None = None) -> int:
    """按 snapshot rows 与前台观察集并集批量 ack；空集短路，异常向上抛。

    两段均幂等（handled_at IS NULL 谓词），重叠行第二次更新落空。
    """
    if not (snap.rows or snap.observed_terminal):
        return 0
    table = jobs_table
    if table is None:
        from src.dao.bohrium_jobs_table import get_bohrium_jobs_table

        table = get_bohrium_jobs_table()
    affected = 0
    if snap.rows:
        affected += table.mark_handled_by_ids(
            user_id=snap.user_id,
            org_id=snap.org_id,
            session_id=snap.session_id,
            row_ids=tuple(int(j["id"]) for j in snap.rows),
        )
    if snap.observed_terminal:
        affected += table.mark_handled_by_job_keys(
            user_id=snap.user_id,
            org_id=snap.org_id,
            session_id=snap.session_id,
            job_keys=tuple(snap.observed_terminal),
        )
    return affected
