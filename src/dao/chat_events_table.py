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
        self,
        session_id: str,
        limit: Optional[int] = None,
        include_spawn: bool = False,
    ) -> List[Dict]:
        """
        获取会话历史事件列表。按 run（task_id）分组后再按时间排，避免两 pod 并发写时
        （旧 pod 优雅退出期间仍写第一轮、新 pod 写 run_interrupted/重跑）导致两轮事件按 created_at 交错。

        默认仅返回父级事件（spawn_id IS NULL）；include_spawn=True 时包含子 agent 行。
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                spawn_filter = "" if include_spawn else " AND spawn_id IS NULL"
                sql = f'''
                    SELECT id, session_id, source, type, content, task_id, invocation_id, spawn_id, created_at
                    FROM {self.table_name}
                    WHERE session_id = %s{spawn_filter}
                    ORDER BY created_at ASC, id ASC
                '''
                if limit:
                    sql += f' LIMIT {limit}'
                cursor.execute(sql, (session_id,))
                results = cursor.fetchall()
                rows = list(results)
                if not rows:
                    return []
                # 每个 task_id（同一次 run）的首次出现时间，按 run 分组再按时间排，避免两 pod 并发写导致两轮交错
                run_start: Dict[Optional[str], float] = {}
                for row in rows:
                    tid = row.get('task_id')
                    ts = row['created_at'].timestamp() if row.get('created_at') else 0.0
                    if tid not in run_start or ts < run_start[tid]:
                        run_start[tid] = ts

                def sort_key(row):
                    tid = row.get('task_id')
                    ts = row['created_at'].timestamp() if row.get('created_at') else 0.0
                    return (run_start.get(tid, ts), ts, row.get('id', 0))

                rows.sort(key=sort_key)
                events = []
                for row in rows:
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
                        'invocation_id': row.get('invocation_id'),
                        'spawn_id': row.get('spawn_id'),
                    }
                    # 供刷新后回放时计算 stream_started_at / elapsed_ms
                    if row.get('created_at') is not None:
                        ev['created_at_ms'] = int(row['created_at'].timestamp() * 1000)
                    # 仅 User/query：存的是 { content, files?, workspace_paths? } 时拆成顶层供前端分开展示。
                    # assistant_state 等事件的 content 也是含 'content' 键的 dict，不可在此拆包，否则会丢掉
                    # tool_calls/meta，并把整段 AssistantMessage 误当成内层字符串传给下游。
                    if (
                        row.get('source') == 'User'
                        and row.get('type') == 'query'
                        and isinstance(content, dict)
                        and 'content' in content
                    ):
                        ev['content'] = content.get('content', '')
                        ev['files'] = content.get('files', [])
                        ev['workspace_paths'] = content.get('workspace_paths', [])
                    events.append(ev)
                return events

    def get_last_user_query(self, session_id: str) -> Optional[Dict]:
        """
        获取该会话最后一次用户输入（source=User, type=query），用于部署中断后重跑。
        返回 dict：content(str), files(list 可选), workspace_paths(list 可选), mode(str 可选), task_id 可选。
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f'''
                    SELECT session_id, source, type, content, task_id, invocation_id, created_at
                    FROM {self.table_name}
                    WHERE session_id = %s AND source = %s AND type = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    ''',
                    (session_id, 'User', 'query'),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                try:
                    content = json.loads(row['content'])
                except (json.JSONDecodeError, TypeError):
                    content = row['content']
                base = {
                    'task_id': row.get('task_id'),
                    'invocation_id': row.get('invocation_id'),
                }
                if isinstance(content, dict):
                    return {
                        'content': (content.get('content') or ''),
                        'files': content.get('files') or [],
                        'workspace_paths': content.get('workspace_paths') or [],
                        'mode': content.get('mode') or 'direct',
                        **base,
                    }
                return {
                    'content': content if isinstance(content, str) else '',
                    'files': [],
                    'workspace_paths': [],
                    'mode': 'direct',
                    **base,
                }

    def add_event(
        self,
        session_id: str,
        source: str,
        event_type: str,
        content: any,
        *,
        task_id: Optional[str] = None,
        invocation_id: Optional[str] = None,
        spawn_id: Optional[str] = None,
    ) -> bool:
        """添加事件到数据库。invocation_id 为本轮调用标识；spawn_id 标记子 agent 事件（NULL=父级）。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                # 将 content 转换为 JSON 字符串
                content_json = json.dumps(content, ensure_ascii=False)

                cursor.execute(
                    f'''
                    INSERT INTO {self.table_name}
                    (session_id, source, type, content, task_id, invocation_id, spawn_id, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    ''',
                    (
                        session_id,
                        source,
                        event_type,
                        content_json,
                        task_id,
                        invocation_id,
                        spawn_id,
                    ),
                )
                conn.commit()
                return cursor.rowcount > 0


@lru_cache
def get_chat_events_table() -> ChatEventsTable:
    return ChatEventsTable()
