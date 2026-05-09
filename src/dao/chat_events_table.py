import json
import logging
from functools import lru_cache

from src.base.base_table import BaseTable

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ChatEventsTable(BaseTable):
    """聊天事件表"""

    table_name = 'evo_chat_events'

    @staticmethod
    def _row_to_event(row: dict) -> dict:
        try:
            content = json.loads(row['content'])
        except (json.JSONDecodeError, TypeError):
            content = row['content']

        ev = {
            'id': row.get('id'),
            'source': row['source'],
            'type': row['type'],
            'content': content,
            'session_id': row['session_id'],
            'task_id': row.get('task_id'),
            'invocation_id': row.get('invocation_id'),
            'spawn_id': row.get('spawn_id'),
        }
        if row.get('created_at') is not None:
            ev['created_at_ms'] = int(row['created_at'].timestamp() * 1000)

        if (
            row.get('source') == 'User'
            and row.get('type') == 'query'
            and isinstance(content, dict)
            and 'content' in content
        ):
            ev['content'] = content.get('content', '')
            ev['files'] = content.get('files', [])
            ev['images'] = content.get('images', [])
            ev['workspace_paths'] = content.get('workspace_paths', [])
            if content.get('session_directory'):
                ev['session_directory'] = content.get('session_directory')
            if content.get('session_directory_source'):
                ev['session_directory_source'] = content.get('session_directory_source')

        return ev

    def get_history_checkpoints(
        self,
        session_id: str,
        spawn_id: str | None,
        limit: int = 5,
    ) -> list[dict]:
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                if spawn_id is None:
                    spawn_filter = ' AND spawn_id IS NULL'
                    params = (session_id,)
                else:
                    spawn_filter = ' AND spawn_id = %s'
                    params = (session_id, spawn_id)

                sql = f'''
                    SELECT id, session_id, source, type, content, task_id, invocation_id, spawn_id, created_at
                    FROM {self.table_name}
                    WHERE session_id = %s
                      AND type = 'history_checkpoint'
                      {spawn_filter}
                    ORDER BY id DESC
                '''
                if limit:
                    sql += f' LIMIT {limit}'
                cursor.execute(sql, params)
                rows = list(cursor.fetchall())
                return [self._row_to_event(row) for row in rows]

    def get_scope_events_after_id(
        self,
        session_id: str,
        spawn_id: str | None,
        after_id: int,
        limit: int | None = None,
    ) -> list[dict]:
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                if spawn_id is None:
                    spawn_filter = ' AND spawn_id IS NULL'
                    params = (session_id, after_id)
                else:
                    spawn_filter = ' AND spawn_id = %s'
                    params = (session_id, spawn_id, after_id)

                sql = f'''
                    SELECT id, session_id, source, type, content, task_id, invocation_id, spawn_id, created_at
                    FROM {self.table_name}
                    WHERE session_id = %s
                      {spawn_filter}
                      AND id > %s
                      AND type NOT IN ('history_checkpoint', 'compaction', 'context_compaction')
                    ORDER BY created_at ASC, id ASC
                '''
                if limit:
                    sql += f' LIMIT {limit}'
                cursor.execute(sql, params)
                rows = list(cursor.fetchall())
                return [self._row_to_event(row) for row in rows]

    def get_latest_scope_event_id(self, session_id: str, spawn_id: str | None) -> int:
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                if spawn_id is None:
                    spawn_filter = 'AND spawn_id IS NULL'
                    params = (session_id,)
                else:
                    spawn_filter = 'AND spawn_id = %s'
                    params = (session_id, spawn_id)

                cursor.execute(
                    f'''
                    SELECT COALESCE(MAX(id), 0) AS latest_event_id
                    FROM {self.table_name}
                    WHERE session_id = %s
                      {spawn_filter}
                      AND type NOT IN ('history_checkpoint', 'compaction', 'context_compaction')
                    ''',
                    params,
                )
                row = cursor.fetchone()
                if not row:
                    return 0
                return int(row.get('latest_event_id') or 0)

    def get_session_events(
        self,
        session_id: str,
        limit: int | None = None,
        include_spawn: bool = False,
    ) -> list[dict]:
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
                run_start: dict[str | None, float] = {}
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
                        ev['images'] = content.get('images', [])
                        ev['workspace_paths'] = content.get('workspace_paths', [])
                        if content.get('session_directory'):
                            ev['session_directory'] = content.get('session_directory')
                        if content.get('session_directory_source'):
                            ev['session_directory_source'] = content.get(
                                'session_directory_source'
                            )
                    events.append(ev)
                return events

    def get_session_user_query_events(self, session_id: str) -> list[dict]:
        """获取父级 User/query 历史事件，供附件清单等轻量查询使用。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f'''
                    SELECT id, session_id, source, type, content, task_id, invocation_id, spawn_id, created_at
                    FROM {self.table_name}
                    WHERE session_id = %s
                      AND source = 'User'
                      AND type = 'query'
                      AND spawn_id IS NULL
                    ORDER BY created_at ASC, id ASC
                    ''',
                    (session_id,),
                )
                return [self._row_to_event(row) for row in list(cursor.fetchall())]

    def get_last_user_query(self, session_id: str) -> dict | None:
        """
        获取该会话最后一次用户输入（source=User, type=query），用于部署中断后重跑。
        返回 dict：content(str), files(list 可选), images(list 可选), workspace_paths(list 可选), mode(str 可选), session_directory(str|None), session_directory_source(str), task_id 可选。
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
                        'images': content.get('images') or [],
                        'workspace_paths': content.get('workspace_paths') or [],
                        'mode': content.get('mode') or 'direct',
                        'session_directory': content.get('session_directory'),
                        'session_directory_source': content.get(
                            'session_directory_source'
                        )
                        or 'none',
                        **base,
                    }
                return {
                    'content': content if isinstance(content, str) else '',
                    'files': [],
                    'images': [],
                    'workspace_paths': [],
                    'mode': 'direct',
                    'session_directory': None,
                    'session_directory_source': 'none',
                    **base,
                }

    def add_event(
        self,
        session_id: str,
        source: str,
        event_type: str,
        content: any,
        *,
        task_id: str | None = None,
        invocation_id: str | None = None,
        spawn_id: str | None = None,
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

    def add_history_checkpoint(
        self,
        session_id: str,
        *,
        task_id: str | None,
        invocation_id: str | None,
        spawn_id: str | None,
        covered_until_event_id: int,
        base_messages: list[dict],
        reason: str = 'summary',
    ) -> bool:
        checkpoint_content = {
            'covered_until_event_id': covered_until_event_id,
            'base_messages': base_messages,
            'reason': reason,
        }

        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f'''
                    INSERT INTO {self.table_name}
                    (session_id, source, type, content, task_id, invocation_id, spawn_id, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    ''',
                    (
                        session_id,
                        'System',
                        'history_checkpoint',
                        json.dumps(checkpoint_content, ensure_ascii=False),
                        task_id,
                        invocation_id,
                        spawn_id,
                    ),
                )
                conn.commit()
                return cursor.rowcount > 0

    def add_checkpoint_pair(
        self,
        session_id: str,
        *,
        task_id: str | None,
        invocation_id: str | None,
        spawn_id: str | None,
        covered_until_event_id: int,
        base_messages: list[dict],
        reason: str = 'summary',
    ) -> bool:
        return self.add_history_checkpoint(
            session_id,
            task_id=task_id,
            invocation_id=invocation_id,
            spawn_id=spawn_id,
            covered_until_event_id=covered_until_event_id,
            base_messages=base_messages,
            reason=reason,
        )

    def get_bohrium_events(self, session_id: str) -> list[dict]:
        """Return paired Bohrium tool call/result events for registry rebuild.

        Intentionally does not apply a LIMIT: truncating the session history can
        orphan submit/poll/download pairs and rebuild an inconsistent registry.
        If this query becomes a hotspot, optimize with indexing or a bounded
        window strategy that still preserves complete Bohrium event pairs.
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f'''
                    SELECT type, content, created_at
                    FROM {self.table_name}
                    WHERE session_id = %s
                      AND type IN ('tool_call', 'tool_result')
                    ORDER BY created_at ASC, id ASC
                    ''',
                    (session_id,),
                )
                rows = cursor.fetchall()

        calls: dict[str, dict] = {}
        results: list[dict] = []

        for row in rows:
            try:
                content = (
                    json.loads(row['content'])
                    if isinstance(row['content'], str)
                    else row['content']
                )
            except (json.JSONDecodeError, TypeError):
                continue

            if not isinstance(content, dict) or content.get('name') != 'Bohrium':
                continue

            call_id = str(content.get('call_id') or content.get('id') or '')
            if not call_id:
                continue

            if row['type'] == 'tool_call':
                args = content.get('args', {})
                if not isinstance(args, dict):
                    args = {}
                calls[call_id] = {
                    'action': str(args.get('action') or ''),
                    'job_name': str(args.get('job_name') or ''),
                }
                continue

            if row['type'] != 'tool_result':
                continue

            call_info = calls.get(call_id, {})
            action = call_info.get('action', '')
            if not action:
                continue

            if content.get('status') == 'error':
                continue

            result_raw = content.get('result', '')
            try:
                result_data = (
                    json.loads(result_raw)
                    if isinstance(result_raw, str)
                    else result_raw
                )
            except (json.JSONDecodeError, TypeError):
                result_data = {}
            if not isinstance(result_data, dict):
                result_data = {}

            results.append(
                {
                    'action': action,
                    'job_id': str(result_data.get('job_id') or ''),
                    'status': str(result_data.get('status') or ''),
                    'job_name': call_info.get('job_name', ''),
                    'cached': bool(result_data.get('cached', False)),
                }
            )

        return results


@lru_cache
def get_chat_events_table() -> ChatEventsTable:
    return ChatEventsTable()
