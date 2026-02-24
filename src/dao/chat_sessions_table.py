import json
import logging
from functools import lru_cache
from typing import Dict, List, Optional

from pymysql import Error

from src.base.base_table import BaseTable

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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

    def get_session(self, session_id: str) -> Optional[Dict]:
        """获取会话信息"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f'''
                    SELECT session_id, user_id, status, is_shared, created_at, updated_at
                    FROM {self.table_name}
                    WHERE session_id = %s
                    ''',
                    (session_id,),
                )
                result = cursor.fetchone()
                return result

    def set_session_status(self, session_id: str, status: str) -> bool:
        """设置会话状态：idle=空闲/已结束，active=运行中"""
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

    def list_sessions(self, user_id: str) -> List[Dict]:
        """获取会话列表，只返回该用户的会话，包含第一条用户消息"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                # 使用子查询获取第一条用户消息，并带上会话 status
                cursor.execute(
                    f'''
                    SELECT s.session_id,
                           s.status,
                           COUNT(e.id) as history_length,
                           (SELECT e2.content
                            FROM evo_chat_events e2
                            WHERE e2.session_id = s.session_id
                              AND e2.source = 'User'
                              AND e2.type = 'query'
                            ORDER BY e2.created_at ASC
                            LIMIT 1) as first_message
                    FROM {self.table_name} s
                    LEFT JOIN evo_chat_events e ON s.session_id = e.session_id
                    WHERE s.user_id = %s
                    GROUP BY s.session_id, s.status
                    ORDER BY s.created_at DESC
                    ''',
                    (user_id,),
                )
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
                            'status': row.get('status', 'idle'),
                            'history_length': row['history_length'],
                            'first_user_message': first_user_message,
                        }
                    )
                return sessions


@lru_cache
def get_chat_sessions_table() -> ChatSessionsTable:
    return ChatSessionsTable()
