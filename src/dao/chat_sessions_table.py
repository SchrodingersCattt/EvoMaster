import json
import logging
from functools import lru_cache

from pymysql import Error

from src.base.base_table import BaseTable

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class _WorkspacePrefUnset:
    """update_session_workspace_prefs 未传入的字段不更新。"""


WORKSPACE_PREF_UNSET = _WorkspacePrefUnset()


class ChatSessionsTable(BaseTable):
    """聊天会话表"""

    table_name = 'evo_chat_sessions'

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
                    f'SELECT id FROM {self.table_name} WHERE session_id = %s',
                    (session_id,),
                )
                if cursor.fetchone():
                    logger.debug(f'会话 {session_id} 已存在')
                    return True

                # 创建新会话（默认 status=idle, is_shared=0）
                cursor.execute(
                    f'''
                    INSERT INTO {self.table_name}
                    (session_id, user_id, status, is_shared, created_at, updated_at)
                    VALUES (%s, %s, 'idle', 0, NOW(), NOW())
                    ''',
                    (session_id, user_id),
                )
                conn.commit()
                logger.info(f'创建会话成功: {session_id}')
                return cursor.rowcount > 0

    def get_session(self, session_id: str) -> dict | None:
        """获取会话信息（含 user_id、org_id、project_id、status 等）。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f'''
                    SELECT session_id, user_id, org_id, project_id, session_directory,
                           chat_mode, status, is_shared, last_task_id, created_at, updated_at
                    FROM {self.table_name}
                    WHERE session_id = %s
                    ''',
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
                        updates.append('org_id = %s')
                        params.append(
                            org_id.strip() if isinstance(org_id, str) else org_id
                        )
                    if project_id is not None:
                        updates.append('project_id = %s')
                        params.append(int(project_id))
                    if not updates:
                        return True
                    params.append(session_id)
                    cursor.execute(
                        f'''
                        UPDATE {self.table_name}
                        SET {', '.join(updates)}, updated_at = NOW()
                        WHERE session_id = %s
                        ''',
                        tuple(params),
                    )
                    conn.commit()
                    return True
        except Error as e:
            logger.warning(
                'set_session_bohrium failed session_id=%s: %s',
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
            sets.append('session_directory = %s')
            params.append(norm_d)
        if not isinstance(chat_mode, _WorkspacePrefUnset):
            norm_m: str | None = None
            if chat_mode is not None:
                m = str(chat_mode).strip().lower()
                if m not in ('direct', 'planner'):
                    logger.warning(
                        'update_session_workspace_prefs invalid chat_mode=%r session_id=%s',
                        chat_mode,
                        session_id,
                    )
                    return False
                norm_m = m
            sets.append('chat_mode = %s')
            params.append(norm_m)
        if not sets:
            return True
        params.extend([session_id, user_id])
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f'''
                        UPDATE {self.table_name}
                        SET {', '.join(sets)}, updated_at = NOW()
                        WHERE session_id = %s AND user_id = %s
                        ''',
                        tuple(params),
                    )
                    conn.commit()
                    return cursor.rowcount > 0
        except Error as e:
            logger.warning(
                'update_session_workspace_prefs failed session_id=%s: %s',
                session_id,
                e,
            )
            return False

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

    def set_session_status(self, session_id: str, status: str) -> bool:
        """设置会话状态：idle=空闲/已结束，active=运行中，waiting=已入队等待 worker 接手"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f'''
                        UPDATE {self.table_name}
                        SET status = %s, updated_at = NOW()
                        WHERE session_id = %s
                        ''',
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
                    f'''
                    UPDATE {self.table_name}
                    SET last_task_id = %s, updated_at = NOW()
                    WHERE session_id = %s
                    ''',
                    (task_id, session_id),
                )
                conn.commit()
                return True

    def set_share_status(self, session_id: str, is_shared: bool, user_id: str) -> bool:
        """设置会话是否分享。若传 user_id 则仅当会话属于该用户时更新。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f'''
                    UPDATE {self.table_name}
                    SET is_shared = %s, updated_at = NOW()
                    WHERE session_id = %s AND user_id = %s
                    ''',
                    (1 if is_shared else 0, session_id, user_id),
                )
                conn.commit()
                return cursor.rowcount > 0

    def delete_session(self, session_id: str, user_id: str) -> bool:
        """删除会话。仅当会话属于该用户时删除（evo_chat_events 有 ON DELETE CASCADE 会级联删除）。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f'DELETE FROM {self.table_name} WHERE session_id = %s AND user_id = %s',
                    (session_id, user_id),
                )
                conn.commit()
                return cursor.rowcount > 0

    def count_active_sessions(self) -> int:
        """统计所有用户的活跃会话数量（status='active'）"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f'''
                    SELECT COUNT(*) as cnt
                    FROM {self.table_name}
                    WHERE status = %s
                    ''',
                    ('active',),
                )
                row = cursor.fetchone()
                return int(row['cnt']) if row and row.get('cnt') is not None else 0

    def reset_all_active_to_idle(self) -> int:
        """
        将当前所有 status='active' 的会话重置为 'idle'。
        用于部署/重启后清理上一进程未正确 release 的残留 active 会话。
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f'''
                    UPDATE {self.table_name}
                    SET status = 'idle', updated_at = NOW()
                    WHERE status = %s
                    ''',
                    ('active',),
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
                sql = f'SELECT COUNT(*) as n FROM {self.table_name} WHERE user_id = %s'
                params: list[object] = [user_id]
                if project_id is not None:
                    sql += ' AND project_id = %s'
                    params.append(int(project_id))
                cursor.execute(
                    sql,
                    tuple(params),
                )
                row = cursor.fetchone()
                return int(row['n']) if row else 0

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
                where_clause = 'WHERE s.user_id = %s'
                params: list[object] = [user_id]
                if project_id is not None:
                    where_clause += ' AND s.project_id = %s'
                    params.append(int(project_id))
                # 不要用 LEFT JOIN 全表事件再 COUNT：事件多时中间结果爆炸。history_length 用标量子查询按 session_id 计数。
                sql = f'''
                    SELECT s.session_id,
                           s.project_id,
                           s.status,
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
                '''
                params.extend([limit, offset])
                cursor.execute(sql, tuple(params))
                results = cursor.fetchall()
                sessions = []
                for row in results:
                    first_user_message = None
                    if row.get('first_message'):
                        try:
                            # first_message 是 JSON 字符串（content 字段存储时会被 json.dumps）
                            content = json.loads(row['first_message'])
                            # content 通常是字符串，直接使用
                            if isinstance(content, str):
                                first_user_message = content
                            else:
                                # 如果不是字符串，转换为字符串
                                first_user_message = str(content)
                        except (json.JSONDecodeError, TypeError):
                            # 如果解析失败，直接使用原始字符串
                            first_user_message = (
                                str(row['first_message'])
                                if row['first_message']
                                else None
                            )

                    sessions.append(
                        {
                            'id': row['session_id'],
                            'project_id': row.get('project_id'),
                            'status': row.get('status', 'idle'),
                            'history_length': row['history_length'],
                            'first_user_message': first_user_message,
                        }
                    )
                return sessions

    def list_sessions_for_project_with_workspace(
        self,
        user_id: str,
        project_id: int,
        limit: int,
    ) -> list[dict]:
        """
        列出某项目下当前用户的会话，含 session_directory、updated_at，用于按目录聚合。
        按 created_at 倒序，最多返回 limit 条。
        """
        limit = max(1, min(2000, limit))
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                where_clause = 'WHERE s.user_id = %s AND s.project_id = %s'
                params: list[object] = [user_id, int(project_id)]
                sql = f'''
                    SELECT s.session_id,
                           s.project_id,
                           s.status,
                           s.session_directory,
                           s.updated_at,
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
                    LIMIT %s
                '''
                params.append(limit)
                cursor.execute(sql, tuple(params))
                results = cursor.fetchall()
                sessions = []
                for row in results:
                    first_user_message = None
                    if row.get('first_message'):
                        try:
                            content = json.loads(row['first_message'])
                            if isinstance(content, str):
                                first_user_message = content
                            else:
                                first_user_message = str(content)
                        except (json.JSONDecodeError, TypeError):
                            first_user_message = (
                                str(row['first_message'])
                                if row['first_message']
                                else None
                            )

                    sessions.append(
                        {
                            'id': row['session_id'],
                            'project_id': row.get('project_id'),
                            'status': row.get('status', 'idle'),
                            'history_length': row['history_length'],
                            'first_user_message': first_user_message,
                            'session_directory': row.get('session_directory'),
                            'updated_at': row.get('updated_at'),
                        }
                    )
                return sessions


@lru_cache
def get_chat_sessions_table() -> ChatSessionsTable:
    return ChatSessionsTable()
