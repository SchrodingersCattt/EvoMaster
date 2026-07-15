"""Admin-only endpoints for session trajectory analysis.

All endpoints require the caller to be in MatMaster platform ``allowlist.admin``.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel

from clients.matmaster_platform.allowlist import is_user_in_admin_allowlist
from src.dao.chat_events_table import ChatEventsTable
from src.dao.chat_sessions_table import ChatSessionsTable
from src.services.sessions_service import ChatSessionsService, get_sessions_service
from src.services.user_service import UserService
from src.utils.exceptions import ForbiddenErrorResponse

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

router = APIRouter(tags=["Admin Chat Sessions"])
user_router = APIRouter(tags=["Admin Users"])


def _require_admin(user_id: str = Depends(UserService.require_user_id)) -> str:
    if not is_user_in_admin_allowlist(user_id):
        raise ForbiddenErrorResponse(msg="Admin access required")
    return user_id


_SORT_COLUMNS = {
    "last_event_at": "e.last_event_at",
    "created_at": "s.created_at",
    "event_count": "e.ec",
}


def _resolve_sort(sort_by: str, order: str) -> str:
    col = _SORT_COLUMNS.get(sort_by, "e.last_event_at")
    direction = "ASC" if order.lower() == "asc" else "DESC"
    return f"{col} {direction}, s.created_at {direction}"


# ─── Response Models ──────────────────────────────────────────────────────────


class SessionSummary(BaseModel):
    session_id: str
    user_id: str | None = None
    project_id: int | None = None
    status: str | None = None
    session_directory: str | None = None
    event_count: int = 0
    last_event_at: int | None = None
    created_at: int | None = None
    updated_at: int | None = None


class AdminSessionListResponse(BaseModel):
    sessions: list[SessionSummary]
    total: int
    limit: int
    offset: int


class AdminSessionEventsResponse(BaseModel):
    session_id: str
    events: list[dict]
    max_event_id: int
    total: int


class AdminRunSessionSummary(BaseModel):
    session_id: str
    project_id: int | None = None
    status: str | None = None
    worker_id: str | None = None
    updated_at: int | None = None


class AdminUserRunStatusListItem(BaseModel):
    user_id: str
    redis_enabled: bool
    running_count: int
    queued_count: int
    stale_count: int
    latest_updated_at: int | None = None
    running_sessions: list[AdminRunSessionSummary]
    queued_sessions: list[AdminRunSessionSummary]
    stale_sessions: list[AdminRunSessionSummary]


class AdminUserRunStatusListResponse(BaseModel):
    items: list[AdminUserRunStatusListItem]
    total: int
    page: int
    page_size: int
    redis_enabled: bool


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=AdminSessionListResponse,
    summary="列出全量会话（admin）",
    description="管理员接口：按时间范围列出所有用户的会话，含事件计数和最后活跃时间。",
    operation_id="adminListSessions",
)
def admin_list_sessions(
    _admin: str = Depends(_require_admin),
    since: datetime | None = Query(None, description="起始时间 (ISO 8601)"),
    until: datetime | None = Query(None, description="截止时间 (ISO 8601)"),
    min_events: int = Query(2, ge=0, description="最少事件数，过滤空会话"),
    user_id: str | None = Query(None, description="按用户 ID 筛选"),
    sort_by: str = Query(
        "last_event_at",
        description="排序字段: last_event_at, created_at, event_count",
    ),
    order: str = Query("desc", description="排序方向: asc, desc"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(days=7)
    if until is None:
        until = datetime.now(timezone.utc)

    sessions_table = ChatSessionsTable()
    events_table = ChatEventsTable()

    with sessions_table.get_connection() as conn:
        with conn.cursor() as cursor:
            where_clauses = ["s.created_at >= %s", "s.created_at <= %s"]
            params: list = [since, until]

            if user_id:
                where_clauses.append("s.user_id = %s")
                params.append(user_id)

            where_sql = " AND ".join(where_clauses)

            count_sql = f"""
                SELECT COUNT(*) AS total
                FROM {sessions_table.table_name} s
                LEFT JOIN (
                    SELECT session_id, COUNT(*) AS ec
                    FROM {events_table.table_name}
                    GROUP BY session_id
                ) e ON e.session_id = s.session_id
                WHERE {where_sql}
                  AND COALESCE(e.ec, 0) >= %s
            """
            cursor.execute(count_sql, (*params, min_events))
            total = cursor.fetchone()["total"]

            sql = f"""
                SELECT s.session_id,
                       s.user_id,
                       s.project_id,
                       s.status,
                       s.session_directory,
                       s.created_at,
                       s.updated_at,
                       COALESCE(e.ec, 0) AS event_count,
                       e.last_event_at
                FROM {sessions_table.table_name} s
                LEFT JOIN (
                    SELECT session_id,
                           COUNT(*) AS ec,
                           MAX(created_at) AS last_event_at
                    FROM {events_table.table_name}
                    GROUP BY session_id
                ) e ON e.session_id = s.session_id
                WHERE {where_sql}
                  AND COALESCE(e.ec, 0) >= %s
                ORDER BY {_resolve_sort(sort_by, order)}
                LIMIT %s OFFSET %s
            """
            cursor.execute(sql, (*params, min_events, limit, offset))
            rows = cursor.fetchall()

    sessions = []
    for row in rows:
        created_at_ms = (
            int(row["created_at"].timestamp() * 1000) if row.get("created_at") else None
        )
        updated_at_ms = (
            int(row["updated_at"].timestamp() * 1000) if row.get("updated_at") else None
        )
        last_event_at_ms = (
            int(row["last_event_at"].timestamp() * 1000)
            if row.get("last_event_at")
            else None
        )
        sessions.append(
            SessionSummary(
                session_id=row["session_id"],
                user_id=row.get("user_id"),
                project_id=row.get("project_id"),
                status=row.get("status"),
                session_directory=row.get("session_directory"),
                event_count=row["event_count"],
                last_event_at=last_event_at_ms,
                created_at=created_at_ms,
                updated_at=updated_at_ms,
            )
        )

    return AdminSessionListResponse(
        sessions=sessions, total=total, limit=limit, offset=offset
    )


@user_router.get(
    "/sessions/run-status",
    response_model=AdminUserRunStatusListResponse,
    summary="列出用户会话运行状态（admin）",
    description="管理员接口：分页列出当前有运行态候选会话的用户，可按 user_id 精确筛选；每行经 Redis owner、queue 与 worker 心跳复核。",
    operation_id="adminListUserRunStatuses",
)
def admin_list_user_run_statuses(
    _admin: str = Depends(_require_admin),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
    user_id: str | None = Query(None, description="按用户 ID 精确筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    return AdminUserRunStatusListResponse(
        **chat_svc.list_user_run_statuses(
            user_id=user_id,
            page=page,
            page_size=page_size,
        )
    )


@router.get(
    "/{session_id}/events",
    response_model=AdminSessionEventsResponse,
    summary="获取会话全量事件（admin）",
    description="管理员接口：获取指定会话的全部事件，支持增量拉取。",
    operation_id="adminGetSessionEvents",
)
def admin_get_session_events(
    session_id: str = Path(..., description="会话 ID"),
    _admin: str = Depends(_require_admin),
    after_event_id: int = Query(
        0, ge=0, description="增量拉取：只返回 id > 此值的事件"
    ),
    include_spawn: bool = Query(True, description="是否包含子 agent 事件"),
    limit: int | None = Query(None, ge=1, le=10000, description="最大返回条数"),
):
    import json as _json

    events_table = ChatEventsTable()

    with events_table.get_connection() as conn:
        with conn.cursor() as cursor:
            spawn_filter = "" if include_spawn else " AND spawn_id IS NULL"
            sql = f"""
                SELECT id, session_id, source, type, content,
                       task_id, invocation_id, spawn_id, created_at
                FROM {events_table.table_name}
                WHERE session_id = %s
                  AND id > %s
                  {spawn_filter}
                ORDER BY created_at ASC, id ASC
            """
            params: list = [session_id, after_event_id]
            if limit:
                sql += " LIMIT %s"
                params.append(limit)
            cursor.execute(sql, tuple(params))
            rows = list(cursor.fetchall())

    events = []
    max_event_id = 0
    for row in rows:
        try:
            content = _json.loads(row["content"])
        except (_json.JSONDecodeError, TypeError):
            content = row["content"]
        ev = {
            "id": row.get("id"),
            "source": row["source"],
            "type": row["type"],
            "content": content,
            "session_id": row["session_id"],
            "task_id": row.get("task_id"),
            "invocation_id": row.get("invocation_id"),
            "spawn_id": row.get("spawn_id"),
        }
        if row.get("created_at") is not None:
            ev["created_at_ms"] = int(row["created_at"].timestamp() * 1000)
        events.append(ev)
        eid = row.get("id") or 0
        if eid > max_event_id:
            max_event_id = eid

    return AdminSessionEventsResponse(
        session_id=session_id,
        events=events,
        max_event_id=max_event_id,
        total=len(events),
    )
