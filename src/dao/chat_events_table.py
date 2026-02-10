import json
import logging
import threading
from typing import Dict, List, Optional

from pymysql import Error

from src.base.base_table import BaseTable

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 聊天事件表实例（延迟初始化，线程安全单例）
_chat_events_table_instance = None
_chat_events_table_lock = threading.Lock()


def get_chat_events_table() -> Optional['ChatEventsTable']:
    """
    获取聊天事件表实例（延迟初始化，线程安全单例）

    Returns:
        ChatEventsTable 实例，如果初始化失败则返回 None
    """
    global _chat_events_table_instance
    if _chat_events_table_instance is None:
        with _chat_events_table_lock:
            if _chat_events_table_instance is None:
                try:
                    _chat_events_table_instance = ChatEventsTable()
                    logger.info('ChatEventsTable 初始化成功')
                except Exception as e:
                    logger.error(f'ChatEventsTable 初始化失败: {e}', exc_info=True)
                    _chat_events_table_instance = None
    return _chat_events_table_instance


class ChatEventsTable(BaseTable):
    """聊天事件表"""

    table_name = 'evo_chat_events'

    def add_event(
        self,
        session_id: str,
        source: str,
        event_type: str,
        content: any,
        task_id: Optional[str] = None,
    ) -> bool:
        """添加事件到数据库"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    # 将 content 转换为 JSON 字符串
                    content_json = json.dumps(content, ensure_ascii=False)

                    cursor.execute(
                        f'''
                        INSERT INTO {self.table_name}
                        (session_id, source, type, content, task_id, created_at)
                        VALUES (%s, %s, %s, %s, %s, NOW())
                        ''',
                        (session_id, source, event_type, content_json, task_id),
                    )
                    conn.commit()
                    return cursor.rowcount > 0
        except Error as e:
            logger.error(f"添加事件失败: {e}")
            return False

    def get_session_events(
        self, session_id: str, limit: Optional[int] = None
    ) -> List[Dict]:
        """获取会话历史事件列表"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    sql = f'''
                        SELECT session_id, source, type, content, task_id, created_at
                        FROM {self.table_name}
                        WHERE session_id = %s
                        ORDER BY created_at ASC
                    '''
                    if limit:
                        sql += f' LIMIT {limit}'
                    cursor.execute(sql, (session_id,))
                    results = cursor.fetchall()
                    events = []
                    for row in results:
                        try:
                            content = json.loads(row['content'])
                        except (json.JSONDecodeError, TypeError):
                            content = row['content']
                        events.append(
                            {
                                'source': row['source'],
                                'type': row['type'],
                                'content': content,
                                'session_id': row['session_id'],
                                'task_id': row.get('task_id'),
                            }
                        )
                    return events
        except Error as e:
            logger.error(f"获取会话历史失败: {e}")
            return []

    def count_by_session(self, session_id: str) -> int:
        """统计某个会话的事件数量"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f'''
                        SELECT COUNT(*) as count
                        FROM {self.table_name}
                        WHERE session_id = %s
                        ''',
                        (session_id,),
                    )
                    result = cursor.fetchone()
                    return result['count'] if result else 0
        except Error as e:
            logger.error(f"统计会话事件数量失败: {e}")
            return 0
