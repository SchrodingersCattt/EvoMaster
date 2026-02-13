import json
import logging
from functools import lru_cache
from typing import Dict, List, Optional

from src.base.base_table import BaseTable

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ChatEventsTable(BaseTable):
    """聊天事件表"""

    table_name = 'evo_chat_events'

    def get_session_events(
        self, session_id: str, limit: Optional[int] = None
    ) -> List[Dict]:
        """获取会话历史事件列表"""
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
                    ev = {
                        'source': row['source'],
                        'type': row['type'],
                        'content': content,
                        'session_id': row['session_id'],
                        'task_id': row.get('task_id'),
                    }
                    # 供刷新后回放时计算 stream_started_at / elapsed_ms
                    if row.get('created_at') is not None:
                        ev['created_at_ms'] = int(row['created_at'].timestamp() * 1000)
                    # User/query 存的是 { content, files } 时拆成顶层 content + files 供前端分开展示
                    if isinstance(content, dict) and 'content' in content:
                        ev['content'] = content.get('content', '')
                        ev['files'] = content.get('files', [])
                    events.append(ev)
                return events

    def add_event(
        self,
        session_id: str,
        source: str,
        event_type: str,
        content: any,
        task_id: Optional[str] = None,
    ) -> bool:
        """添加事件到数据库"""
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


@lru_cache
def get_chat_events_table() -> ChatEventsTable:
    return ChatEventsTable()
