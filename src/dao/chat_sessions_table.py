import json
import logging
import threading
from typing import Dict, List, Optional

from pymysql import Error

from src.base.base_table import BaseTable

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 聊天会话表实例（延迟初始化，线程安全单例）
_chat_sessions_table_instance = None
_chat_sessions_table_lock = threading.Lock()


def get_chat_sessions_table() -> Optional['ChatSessionsTable']:
    """
    获取聊天会话表实例（延迟初始化，线程安全单例）

    Returns:
        ChatSessionsTable 实例，如果初始化失败则返回 None
    """
    global _chat_sessions_table_instance
    if _chat_sessions_table_instance is None:
        with _chat_sessions_table_lock:
            if _chat_sessions_table_instance is None:
                try:
                    _chat_sessions_table_instance = ChatSessionsTable()
                    logger.info('ChatSessionsTable 初始化成功')
                except Exception as e:
                    logger.error(f'ChatSessionsTable 初始化失败: {e}', exc_info=True)
                    _chat_sessions_table_instance = None
    return _chat_sessions_table_instance


class ChatSessionsTable(BaseTable):
    """聊天会话表"""

    table_name = 'evo_chat_sessions'

    def create_session(
        self,
        session_id: str,
        user_id: str,
    ) -> bool:
        """创建新会话"""
        try:
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

                    # 创建新会话
                    cursor.execute(
                        f'''
                        INSERT INTO {self.table_name}
                        (session_id, user_id, created_at, updated_at)
                        VALUES (%s, %s, NOW(), NOW())
                        ''',
                        (session_id, user_id),
                    )
                    conn.commit()
                    logger.info(f'创建会话成功: {session_id}')
                    return cursor.rowcount > 0
        except Error as e:
            logger.error(f"创建会话失败: {e}")
            return False

    def update_session_user_id(self, session_id: str, user_id: Optional[str]) -> bool:
        """更新会话的用户ID"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f'''
                        UPDATE {self.table_name}
                        SET user_id = %s, updated_at = NOW()
                        WHERE session_id = %s
                        ''',
                        (user_id, session_id),
                    )
                    conn.commit()
                    return cursor.rowcount > 0
        except Error as e:
            logger.error(f"更新会话 user_id 失败: {e}")
            return False

    def get_session(self, session_id: str) -> Optional[Dict]:
        """获取会话信息"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f'''
                        SELECT session_id, user_id, created_at, updated_at
                        FROM {self.table_name}
                        WHERE session_id = %s
                        ''',
                        (session_id,),
                    )
                    result = cursor.fetchone()
                    return result
        except Error as e:
            logger.error(f"获取会话失败: {e}")
            return None

    def get_sessions(self, user_id: str) -> List[Dict]:
        """获取会话列表，只返回该用户的会话，包含第一条用户消息"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    # 使用子查询获取第一条用户消息
                    cursor.execute(
                        f'''
                        SELECT s.session_id,
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
                        GROUP BY s.session_id
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
                                'history_length': row['history_length'],
                                'first_user_message': first_user_message,
                            }
                        )
                    return sessions
        except Error as e:
            logger.error(f"获取会话列表失败: {e}")
            return []

    def set_session_last_task(self, session_id: str, task_id: str) -> bool:
        """设置会话的最后一个 task_id"""
        try:
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
        except Error as e:
            logger.error(f"设置会话 task_id 失败: {e}")
            return False

    def get_session_run_info(self, session_id: str) -> Dict:
        """获取会话的 run_id、last_task_id、task_ids"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    # 获取会话信息
                    cursor.execute(
                        f'''
                        SELECT session_id, last_task_id
                        FROM {self.table_name}
                        WHERE session_id = %s
                        ''',
                        (session_id,),
                    )
                    session = cursor.fetchone()
                    if not session:
                        return {
                            'run_id': 'mat_master_web',
                            'last_task_id': None,
                            'task_ids': [],
                        }

                    # 不再从 chat_session_tasks 表查询，直接返回空列表
                    return {
                        'run_id': 'mat_master_web',
                        'last_task_id': session.get('last_task_id'),
                        'task_ids': [],  # 不再使用 chat_session_tasks 表
                    }
        except Error as e:
            logger.error(f"获取会话 run_info 失败: {e}")
            return {
                'run_id': 'mat_master_web',
                'last_task_id': None,
                'task_ids': [],
            }
