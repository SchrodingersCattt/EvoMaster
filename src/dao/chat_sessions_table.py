import json
import logging
from datetime import datetime
from functools import lru_cache
from typing import Any

from pymysql import Error

from src.base.base_table import BaseTable

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _parse_first_message_cell(raw: object) -> str | None:
    if raw is None:
        return None
    try:
        content = json.loads(raw)
        if isinstance(content, str):
            return content
        return str(content)
    except (json.JSONDecodeError, TypeError):
        return str(raw) if raw else None


def _normalize_session_title_cell(raw: object) -> str | None:
    """session_title 列归一化：去空白，空串视为未设置（None）。"""
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def session_row_to_item(row: dict) -> dict:
    """把会话表行映射为对外列表项 DTO。

    扁平列表与按目录分组列表共用：两类查询行都含 session_id、project_id、
    status、session_title、history_length、first_message 等列。
    """
    return {
        "id": row["session_id"],
        "project_id": row.get("project_id"),
        "status": row.get("status", "idle"),
        "title": _normalize_session_title_cell(row.get("session_title")),
        "history_length": int(row.get("history_length") or 0),
        "first_user_message": _parse_first_message_cell(row.get("first_message")),
    }


def _dir_key_expr(alias: str = "s") -> str:
    """SQL 表达式：与业务层 norm_dir 一致，空/NULL → __UNSET__。"""
    return f"COALESCE(NULLIF(TRIM({alias}.session_directory), ''), '__UNSET__')"


def _not_deleted_expr(alias: str = "s") -> str:
    """用户侧查询默认只读取未被软删除的会话。"""
    return f"{alias}.deleted_at IS NULL"


class _WorkspacePrefUnset:
    """update_session_workspace_prefs 未传入的字段不更新。"""


WORKSPACE_PREF_UNSET = _WorkspacePrefUnset()


class ChatSessionsTable(BaseTable):
    """聊天会话表"""

    table_name = "evo_chat_sessions"

    def create_session(
        self,
        session_id: str,
        user_id: str,
    ) -> bool:
        """创建新会话"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                # 检查会话是否已存在
                cursor.execute(
                    f"SELECT id, deleted_at FROM {self.table_name} WHERE session_id = %s",
                    (session_id,),
                )
                existing = cursor.fetchone()
                if existing:
                    if existing.get("deleted_at") is not None:
                        logger.warning(
                            "会话 %s 已被软删除，拒绝复用相同 session_id",
                            session_id,
                        )
                        return False
                    logger.debug(f"会话 {session_id} 已存在")
                    return True

                # 创建新会话（默认 status=idle, is_shared=0）
                cursor.execute(
                    f"""
                    INSERT INTO {self.table_name}
                    (session_id, user_id, status, is_shared, created_at, updated_at)
                    VALUES (%s, %s, 'idle', 0, NOW(), NOW())
                    """,
                    (session_id, user_id),
                )
                conn.commit()
                logger.info(f"创建会话成功: {session_id}")
                return cursor.rowcount > 0

    def get_session(
        self, session_id: str, *, include_deleted: bool = False
    ) -> dict | None:
        """获取会话信息（含 user_id、org_id、project_id、status 等）。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                deleted_filter = "" if include_deleted else "AND deleted_at IS NULL"
                cursor.execute(
                    f"""
                    SELECT session_id, user_id, org_id, project_id, session_directory,
                           chat_mode, session_title, status, is_shared, last_task_id,
                           created_at, updated_at, deleted_at, deleted_by
                    FROM {self.table_name}
                    WHERE session_id = %s
                      {deleted_filter}
                    """,
                    (session_id,),
                )
                result = cursor.fetchone()
                if not result:
                    return result
                return dict(result)

    def set_session_bohrium(
        self,
        session_id: str,
        org_id: str | None = None,
        project_id: int | None = None,
    ) -> bool:
        """更新会话的 org_id、project_id（仅更新非 None 的字段）。"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    updates = []
                    params = []
                    if org_id is not None:
                        updates.append("org_id = %s")
                        params.append(
                            org_id.strip() if isinstance(org_id, str) else org_id
                        )
                    if project_id is not None:
                        updates.append("project_id = %s")
                        params.append(int(project_id))
                    if not updates:
                        return True
                    params.append(session_id)
                    cursor.execute(
                        f"""
                        UPDATE {self.table_name}
                        SET {', '.join(updates)}, updated_at = NOW()
                        WHERE session_id = %s
                        """,
                        tuple(params),
                    )
                    conn.commit()
                    return True
        except Error as e:
            logger.warning(
                "set_session_bohrium failed session_id=%s: %s",
                session_id,
                e,
            )
            return False

    def update_session_workspace_prefs(
        self,
        session_id: str,
        user_id: str,
        *,
        directory: str | None | _WorkspacePrefUnset = WORKSPACE_PREF_UNSET,
        chat_mode: str | None | _WorkspacePrefUnset = WORKSPACE_PREF_UNSET,
    ) -> bool:
        """更新会话工作区目录与/或 chat_mode。未传入的字段不更新。仅所有者可写。"""
        sets: list[str] = []
        params: list[object] = []
        if not isinstance(directory, _WorkspacePrefUnset):
            norm_d: str | None = None
            if directory is not None:
                s = str(directory).strip()
                norm_d = s if s else None
            sets.append("session_directory = %s")
            params.append(norm_d)
        if not isinstance(chat_mode, _WorkspacePrefUnset):
            norm_m: str | None = None
            if chat_mode is not None:
                m = str(chat_mode).strip().lower()
                if m not in ("direct", "planner"):
                    logger.warning(
                        "update_session_workspace_prefs invalid chat_mode=%r session_id=%s",
                        chat_mode,
                        session_id,
                    )
                    return False
                norm_m = m
            sets.append("chat_mode = %s")
            params.append(norm_m)
        if not sets:
            return True
        params.extend([session_id, user_id])
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET {', '.join(sets)}, updated_at = NOW()
                    WHERE session_id = %s AND user_id = %s
                    """,
                    tuple(params),
                )
                conn.commit()
                return cursor.rowcount > 0

    def set_session_directory(
        self,
        session_id: str,
        directory: str | None,
        user_id: str,
    ) -> bool:
        """更新会话绑定目录。仅所有者可写；directory 为 None 或空串则置为 NULL。"""
        return self.update_session_workspace_prefs(
            session_id,
            user_id,
            directory=directory,
            chat_mode=WORKSPACE_PREF_UNSET,
        )

    def set_session_title(
        self,
        session_id: str,
        user_id: str,
        title: str | None,
    ) -> bool:
        """更新会话自定义标题。仅所有者可写；title 为 None 或空串则置为 NULL（回退 first_user_message）。"""
        norm: str | None = None
        if title is not None:
            s = str(title).strip()
            norm = s if s else None
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET session_title = %s, updated_at = NOW()
                    WHERE session_id = %s AND user_id = %s
                    """,
                    (norm, session_id, user_id),
                )
                conn.commit()
                return cursor.rowcount > 0

    def set_session_status(self, session_id: str, status: str) -> bool:
        """设置会话状态：idle=空闲/已结束，active=运行中，waiting=已入队等待 worker 接手"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"""
                        UPDATE {self.table_name}
                        SET status = %s, updated_at = NOW()
                        WHERE session_id = %s
                        """,
                        (status, session_id),
                    )
                    conn.commit()
                    return cursor.rowcount > 0
        except Error as e:
            logger.error(f"设置会话 status 失败: {e}")
            return False

    def set_session_last_task(self, session_id: str, task_id: str) -> bool:
        """设置会话的最后一个 task_id"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                # 更新会话的 last_task_id（不再使用 chat_session_tasks 表）
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET last_task_id = %s, updated_at = NOW()
                    WHERE session_id = %s
                    """,
                    (task_id, session_id),
                )
                conn.commit()
                return True

    def set_share_status(self, session_id: str, is_shared: bool, user_id: str) -> bool:
        """设置会话是否分享。若传 user_id 则仅当会话属于该用户时更新。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET is_shared = %s, updated_at = NOW()
                    WHERE session_id = %s AND user_id = %s
                    """,
                    (1 if is_shared else 0, session_id, user_id),
                )
                conn.commit()
                return cursor.rowcount > 0

    def delete_session(self, session_id: str, user_id: str) -> bool:
        """软删除会话。仅当会话属于该用户且未删除时标记删除；聊天事件保留。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET deleted_at = NOW(), deleted_by = %s, updated_at = NOW()
                    WHERE session_id = %s AND user_id = %s AND deleted_at IS NULL
                    """,
                    (user_id, session_id, user_id),
                )
                conn.commit()
                return cursor.rowcount > 0

    def list_session_delete_candidates_by_directory(
        self,
        user_id: str,
        project_id: int,
        *,
        directory: str | None,
    ) -> list[dict[str, Any]]:
        """返回某目录组下仍未删除的会话，用于整组删除前校验状态。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                where_clause = (
                    "WHERE user_id = %s AND project_id = %s AND deleted_at IS NULL"
                )
                params: list[object] = [user_id, int(project_id)]
                if directory is None:
                    where_clause += (
                        " AND (session_directory IS NULL "
                        "OR TRIM(session_directory) = '')"
                    )
                else:
                    where_clause += " AND session_directory = %s"
                    params.append(directory)
                cursor.execute(
                    f"""
                    SELECT session_id, status
                    FROM {self.table_name}
                    {where_clause}
                    ORDER BY updated_at DESC, session_id DESC
                    """,
                    tuple(params),
                )
                return list(cursor.fetchall() or [])

    def soft_delete_sessions_by_ids(self, session_ids: list[str], user_id: str) -> int:
        """按 session_id 集合软删除当前用户的未删除会话，返回实际标记数量。"""
        ids = [sid.strip() for sid in session_ids if sid and sid.strip()]
        if not ids:
            return 0
        placeholders = ", ".join(["%s"] * len(ids))
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET deleted_at = NOW(), deleted_by = %s, updated_at = NOW()
                    WHERE user_id = %s
                      AND deleted_at IS NULL
                      AND session_id IN ({placeholders})
                    """,
                    tuple([user_id, user_id, *ids]),
                )
                conn.commit()
                return int(cursor.rowcount or 0)

    def count_active_sessions(self) -> int:
        """统计所有用户的活跃会话数量（status='active'）"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT COUNT(*) as cnt
                    FROM {self.table_name}
                    WHERE status = %s AND deleted_at IS NULL
                    """,
                    ("active",),
                )
                row = cursor.fetchone()
                return int(row["cnt"]) if row and row.get("cnt") is not None else 0

    def reset_all_active_to_idle(self) -> int:
        """
        将当前所有 status='active' 的会话重置为 'idle'。
        用于部署/重启后清理上一进程未正确 release 的残留 active 会话。
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET status = 'idle', updated_at = NOW()
                    WHERE status = %s AND deleted_at IS NULL
                    """,
                    ("active",),
                )
                conn.commit()
                return cursor.rowcount or 0

    def count_sessions_by_user(
        self,
        user_id: str,
        project_id: int | None = None,
    ) -> int:
        """获取该用户的会话总数（用于分页，可按 project_id 过滤）。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                sql = (
                    f"SELECT COUNT(*) as n FROM {self.table_name} "
                    "WHERE user_id = %s AND deleted_at IS NULL"
                )
                params: list[object] = [user_id]
                if project_id is not None:
                    sql += " AND project_id = %s"
                    params.append(int(project_id))
                cursor.execute(
                    sql,
                    tuple(params),
                )
                row = cursor.fetchone()
                return int(row["n"]) if row else 0

    def list_sessions(
        self,
        user_id: str,
        limit: int | None = None,
        offset: int | None = None,
        project_id: int | None = None,
    ) -> list[dict]:
        """获取会话列表，只返回该用户的会话，可按 project_id 过滤，包含第一条用户消息。"""
        limit = max(1, min(100, limit)) if limit is not None else 50
        offset = max(0, offset) if offset is not None else 0
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                # 使用子查询获取第一条用户消息，并带上会话 status；分页在应用层排序后做会破坏顺序，故在 SQL 中用子查询分页
                where_clause = f"WHERE s.user_id = %s AND {_not_deleted_expr('s')}"
                params: list[object] = [user_id]
                if project_id is not None:
                    where_clause += " AND s.project_id = %s"
                    params.append(int(project_id))
                # 不要用 LEFT JOIN 全表事件再 COUNT：事件多时中间结果爆炸。history_length 用标量子查询按 session_id 计数。
                sql = f"""
                    SELECT s.session_id,
                           s.project_id,
                           s.status,
                           s.session_title,
                           (SELECT COUNT(*)
                            FROM evo_chat_events e_cnt
                            WHERE e_cnt.session_id = s.session_id) as history_length,
                           (SELECT e2.content
                            FROM evo_chat_events e2
                            WHERE e2.session_id = s.session_id
                              AND e2.source = 'User'
                              AND e2.type = 'query'
                            ORDER BY e2.created_at ASC
                            LIMIT 1) as first_message
                    FROM {self.table_name} s
                    {where_clause}
                    ORDER BY s.created_at DESC
                    LIMIT %s OFFSET %s
                """
                params.extend([limit, offset])
                cursor.execute(sql, tuple(params))
                results = cursor.fetchall()
                return [session_row_to_item(row) for row in results]

    def aggregate_session_directory_stats(
        self,
        user_id: str,
        project_id: int,
    ) -> list[dict[str, Any]]:
        """按目录桶统计会话数与组内最新 updated_at（用于排序与 has_more）。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                dk = _dir_key_expr("s")
                sql = f"""
                    SELECT {dk} AS dk,
                           COUNT(*) AS session_count,
                           MAX(s.updated_at) AS max_upd
                    FROM {self.table_name} s
                    WHERE s.user_id = %s AND s.project_id = %s AND {_not_deleted_expr('s')}
                    GROUP BY dk
                """
                cursor.execute(sql, (user_id, int(project_id)))
                rows = cursor.fetchall() or []
                out: list[dict[str, Any]] = []
                for row in rows:
                    out.append(
                        {
                            "dk": row["dk"],
                            "session_count": int(row["session_count"] or 0),
                            "max_upd": row.get("max_upd"),
                        }
                    )
                return out

    def list_sessions_windowed_first_per_directory(
        self,
        user_id: str,
        project_id: int,
        per_group_limit: int,
    ) -> list[dict[str, Any]]:
        """
        每个 session_directory 桶内按 updated_at DESC 取前 per_group_limit 条（窗口函数）。
        返回行含 dk、history_length、first_message 解析前原始列等。
        """
        cap = max(1, min(50, per_group_limit))
        dk_sql = _dir_key_expr("s")
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                # history_length / first_message 是打在大表 evo_chat_events 上的标量子查询。
                # 必须放在 rn<=cap 过滤“之后”的外层 SELECT，否则会对该用户/项目下“全部会话”
                # 逐个执行（含事件数巨大的会话），是会话列表 ~2s 的主因。挪到外层后，仅对每个
                # 目录组实际返回的前 cap 条执行，次数从“总会话数”降到“目录数 × cap”。
                sql = f"""
                    SELECT t.session_id,
                           t.project_id,
                           t.status,
                           t.session_directory,
                           t.session_title,
                           t.updated_at,
                           (SELECT COUNT(*)
                            FROM evo_chat_events e_cnt
                            WHERE e_cnt.session_id = t.session_id) AS history_length,
                           (SELECT e2.content
                            FROM evo_chat_events e2
                            WHERE e2.session_id = t.session_id
                              AND e2.source = 'User'
                              AND e2.type = 'query'
                            ORDER BY e2.created_at ASC
                            LIMIT 1) AS first_message,
                           t.dk,
                           t.rn
                    FROM (
                        SELECT s.session_id,
                               s.project_id,
                               s.status,
                               s.session_directory,
                               s.session_title,
                               s.updated_at,
                               {dk_sql} AS dk,
                               ROW_NUMBER() OVER (
                                   PARTITION BY {dk_sql}
                                   ORDER BY s.updated_at DESC, s.session_id DESC
                               ) AS rn
                        FROM {self.table_name} s
                        WHERE s.user_id = %s AND s.project_id = %s AND {_not_deleted_expr('s')}
                    ) t
                    WHERE t.rn <= %s
                """
                cursor.execute(sql, (user_id, int(project_id), cap))
                return list(cursor.fetchall() or [])

    def list_sessions_in_directory_paged(
        self,
        user_id: str,
        project_id: int,
        *,
        directory: str | None,
        limit: int,
        cursor_updated_at: datetime | None,
        cursor_session_id: str | None,
    ) -> list[dict[str, Any]]:
        """
        单目录桶内分页。directory is None 表示未设置目录组。
        按 updated_at DESC, session_id DESC；游标为上一页最后一条。
        """
        cap = max(1, min(50, limit))
        fetch_n = cap + 1
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                where_clause = f"WHERE s.user_id = %s AND s.project_id = %s AND {_not_deleted_expr('s')}"
                params: list[object] = [user_id, int(project_id)]
                if directory is None:
                    where_clause += " AND (s.session_directory IS NULL OR TRIM(s.session_directory) = '')"
                else:
                    where_clause += " AND s.session_directory = %s"
                    params.append(directory)
                if cursor_updated_at is not None and cursor_session_id is not None:
                    where_clause += (
                        " AND (s.updated_at < %s OR "
                        "(s.updated_at = %s AND s.session_id < %s))"
                    )
                    params.extend(
                        [cursor_updated_at, cursor_updated_at, cursor_session_id]
                    )
                sql = f"""
                    SELECT s.session_id,
                           s.project_id,
                           s.status,
                           s.session_directory,
                           s.session_title,
                           s.updated_at,
                           (SELECT COUNT(*)
                            FROM evo_chat_events e_cnt
                            WHERE e_cnt.session_id = s.session_id) AS history_length,
                           (SELECT e2.content
                            FROM evo_chat_events e2
                            WHERE e2.session_id = s.session_id
                              AND e2.source = 'User'
                              AND e2.type = 'query'
                            ORDER BY e2.created_at ASC
                            LIMIT 1) AS first_message
                    FROM {self.table_name} s
                    {where_clause}
                    ORDER BY s.updated_at DESC, s.session_id DESC
                    LIMIT %s
                """
                params.append(fetch_n)
                cursor.execute(sql, tuple(params))
                return list(cursor.fetchall() or [])


@lru_cache
def get_chat_sessions_table() -> ChatSessionsTable:
    return ChatSessionsTable()
