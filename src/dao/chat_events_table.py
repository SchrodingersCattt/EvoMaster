import json
import logging
from collections.abc import Iterable
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
            if content.get('requested_llm'):
                ev['requested_llm'] = content.get('requested_llm')
            if content.get('requested_model'):
                ev['requested_model'] = content.get('requested_model')

        return ev

    @staticmethod
    def _row_to_context_event(row: dict) -> dict:
        """Parse DB event rows for context assembly without display flattening.

        Do not reuse `_row_to_event()` here: that helper intentionally flattens
        User/query payloads for frontend display, while ContextAssemblyPorts need
        the raw JSON payload shape for Phase 2B session source reconstruction.
        """
        try:
            content = json.loads(row['content'])
        except (json.JSONDecodeError, TypeError):
            content = row['content']

        event = {
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
            event['created_at_ms'] = int(row['created_at'].timestamp() * 1000)
        return event

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

    def get_recent_context_anchor_events(
        self,
        session_id: str,
        spawn_id: str | None,
        limit: int = 50,
    ) -> list[dict]:
        """Phase 1 临时方法; Phase 2A 由 SessionEventsPort 取代。"""
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
                      AND type IN ('user_turn_context', 'history_checkpoint')
                      {spawn_filter}
                    ORDER BY id DESC
                '''
                if limit:
                    sql += f' LIMIT {int(limit)}'
                cursor.execute(sql, params)
                return [self._row_to_event(row) for row in list(cursor.fetchall())]

    def query_context_events(
        self,
        *,
        session_id: str,
        spawn_id: str | None,
        until_event_id: int | None = None,
        event_types: tuple[str, ...] | None = None,
        limit: int | None = None,
        order: str = 'asc',
    ) -> list[dict]:
        """Read events for context assembly ports.

        Read-only helper used by runtime context event ports.
        """
        if order not in {'asc', 'desc'}:
            raise ValueError("order must be 'asc' or 'desc'")

        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                params: tuple = (session_id,)
                if spawn_id is None:
                    spawn_filter = ' AND spawn_id IS NULL'
                else:
                    spawn_filter = ' AND spawn_id = %s'
                    params = (*params, spawn_id)

                boundary_filter = ''
                if until_event_id is not None:
                    boundary_filter = ' AND id <= %s'
                    params = (*params, until_event_id)

                type_filter = ''
                if event_types:
                    placeholders = ', '.join(['%s'] * len(event_types))
                    type_filter = f' AND type IN ({placeholders})'
                    params = (*params, *event_types)

                order_sql = 'DESC' if order == 'desc' else 'ASC'
                sql = f'''
                    SELECT id, session_id, source, type, content, task_id, invocation_id, spawn_id, created_at
                    FROM {self.table_name}
                    WHERE session_id = %s
                      {spawn_filter}
                      {boundary_filter}
                      {type_filter}
                    ORDER BY id {order_sql}
                '''
                if limit is not None:
                    if limit < 0:
                        raise ValueError('limit must be >= 0')
                    sql += f' LIMIT {int(limit)}'
                cursor.execute(sql, params)
                return [
                    self._row_to_context_event(row) for row in list(cursor.fetchall())
                ]

    def query_user_turn_context_by_invocation(
        self,
        session_id: str,
        invocation_id: str,
        spawn_id: str | None,
    ) -> dict | None:
        """Phase 1 dedup 查询: 按 (session_id, invocation_id, spawn_id) 找已写的
        user_turn_context 事件。命中返回 row dict, 否则 None。

        Phase 1 仅 root spawn 写入, 实际查询固定 spawn_id IS NULL; Phase 2A 起放开。
        DB 层 unique index 留待 Phase 1.5 / 2A 通过 migration 添加。
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                if spawn_id is None:
                    spawn_filter = ' AND spawn_id IS NULL'
                    params = (session_id, invocation_id)
                else:
                    spawn_filter = ' AND spawn_id = %s'
                    params = (session_id, invocation_id, spawn_id)

                sql = f'''
                    SELECT id, session_id, source, type, content, task_id, invocation_id, spawn_id, created_at
                    FROM {self.table_name}
                    WHERE session_id = %s
                      AND invocation_id = %s
                      AND type = 'user_turn_context'
                      {spawn_filter}
                    ORDER BY id ASC
                    LIMIT 1
                '''
                cursor.execute(sql, params)
                row = cursor.fetchone()
                return self._row_to_event(row) if row else None

    def has_user_turn_context(
        self,
        session_id: str,
        spawn_id: str | None,
    ) -> bool:
        """Phase 1 restore 分流查询: session/scope 内是否存在 user_turn_context。

        不要用 get_session_events(limit=N) 做探测；该 DAO 返回最早的 N 条事件，
        长 session 会漏掉后续 Phase 1 写入的 user_turn_context。
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                if spawn_id is None:
                    spawn_filter = ' AND spawn_id IS NULL'
                    params = (session_id,)
                else:
                    spawn_filter = ' AND spawn_id = %s'
                    params = (session_id, spawn_id)

                sql = f'''
                    SELECT 1
                    FROM {self.table_name}
                    WHERE session_id = %s
                      AND type = 'user_turn_context'
                      {spawn_filter}
                    LIMIT 1
                '''
                cursor.execute(sql, params)
                return cursor.fetchone() is not None

    def get_scope_events_after_id(
        self,
        session_id: str,
        spawn_id: str | None,
        after_id: int | None,
        limit: int | None = None,
    ) -> list[dict]:
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                if spawn_id is None:
                    spawn_filter = ' AND spawn_id IS NULL'
                    params: tuple = (session_id,)
                else:
                    spawn_filter = ' AND spawn_id = %s'
                    params = (session_id, spawn_id)

                after_filter = ''
                if after_id is not None:
                    after_filter = ' AND id > %s'
                    params = (*params, after_id)

                sql = f'''
                    SELECT id, session_id, source, type, content, task_id, invocation_id, spawn_id, created_at
                    FROM {self.table_name}
                    WHERE session_id = %s
                      {spawn_filter}
                      {after_filter}
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
        exclude_types: Iterable[str] | None = None,
    ) -> list[dict]:
        """
        获取会话历史事件列表。按 run（task_id）分组后再按时间排，避免两 pod 并发写时
        （旧 pod 优雅退出期间仍写第一轮、新 pod 写 run_interrupted/重跑）导致两轮事件按 created_at 交错。

        默认仅返回父级事件（spawn_id IS NULL）；include_spawn=True 时包含子 agent 行。

        exclude_types：在 SQL 层用 `type NOT IN (...)` 过滤掉这些事件类型，避免读取/反序列化
        注定会被丢弃的大体积行（如 history_checkpoint 的整段上下文快照）。仅回放路径使用。
        """
        exclude_list = [t for t in exclude_types] if exclude_types else []
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                spawn_filter = "" if include_spawn else " AND spawn_id IS NULL"
                type_filter = ""
                params: list = [session_id]
                if exclude_list:
                    placeholders = ', '.join(['%s'] * len(exclude_list))
                    type_filter = f" AND type NOT IN ({placeholders})"
                    params.extend(exclude_list)
                sql = f'''
                    SELECT id, session_id, source, type, content, task_id, invocation_id, spawn_id, created_at
                    FROM {self.table_name}
                    WHERE session_id = %s{spawn_filter}{type_filter}
                    ORDER BY created_at ASC, id ASC
                '''
                if limit:
                    sql += f' LIMIT {limit}'
                cursor.execute(sql, tuple(params))
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
                        if content.get('requested_llm'):
                            ev['requested_llm'] = content.get('requested_llm')
                        if content.get('requested_model'):
                            ev['requested_model'] = content.get('requested_model')
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
                        'requested_llm': content.get('requested_llm'),
                        'requested_model': content.get('requested_model'),
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
                    'requested_llm': None,
                    'requested_model': None,
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
        schema_version: str | None = None,
        render_version: str | None = None,
        user_instructions_text: str | None = None,
        user_instructions_hash: str | None = None,
    ) -> bool:
        checkpoint_content = {
            'covered_until_event_id': covered_until_event_id,
            'base_messages': base_messages,
            'reason': reason,
        }
        optional_metadata = {
            'schema_version': schema_version,
            'render_version': render_version,
            'user_instructions_text': user_instructions_text,
            'user_instructions_hash': user_instructions_hash,
        }
        checkpoint_content.update(
            {
                key: value
                for key, value in optional_metadata.items()
                if value is not None
            }
        )

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
        schema_version: str | None = None,
        render_version: str | None = None,
        user_instructions_text: str | None = None,
        user_instructions_hash: str | None = None,
    ) -> bool:
        return self.add_history_checkpoint(
            session_id,
            task_id=task_id,
            invocation_id=invocation_id,
            spawn_id=spawn_id,
            covered_until_event_id=covered_until_event_id,
            base_messages=base_messages,
            reason=reason,
            schema_version=schema_version,
            render_version=render_version,
            user_instructions_text=user_instructions_text,
            user_instructions_hash=user_instructions_hash,
        )

    def get_last_user_query_event(self, session_id: str) -> dict | None:
        """返回最后一条 User/query 事件的完整行（含 id），用于 replace_last_turn。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f'''
                    SELECT id, session_id, source, type, content, task_id, invocation_id, spawn_id, created_at
                    FROM {self.table_name}
                    WHERE session_id = %s AND source = 'User' AND type = 'query' AND spawn_id IS NULL
                    ORDER BY id DESC
                    LIMIT 1
                    ''',
                    (session_id,),
                )
                row = cursor.fetchone()
                return self._row_to_event(row) if row else None

    def get_last_resolved_model_profile(self, session_id: str) -> str | None:
        """返回该会话最近一条父级 LLM 输出事件解析出的 model_profile。

        仅看 spawn_id IS NULL 的 response / assistant_state 事件，按时间倒序取最近一条。
        若该事件是 BYOK（model_profile == 'byok' 或 model_route 以 'byok:' 开头），
        或 model_profile 字段缺失/为空，返回 None（由调用方落回默认模型链路）。
        判别只看这一条，不向更早历史回溯。
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f'''
                    SELECT content
                    FROM {self.table_name}
                    WHERE session_id = %s
                      AND spawn_id IS NULL
                      AND type IN ('response', 'assistant_state')
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    ''',
                    (session_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                try:
                    content = json.loads(row['content'])
                except (json.JSONDecodeError, TypeError):
                    return None
                if not isinstance(content, dict):
                    return None
                model_route = content.get('model_route') or ''
                if isinstance(model_route, str) and model_route.startswith('byok:'):
                    return None
                profile = content.get('model_profile')
                if not profile or profile == 'byok':
                    return None
                return profile

    def delete_events_from_id(self, session_id: str, from_event_id: int) -> int:
        """物理删除 session 中 id >= from_event_id 的所有事件，返回删除行数。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f'''
                    DELETE FROM {self.table_name}
                    WHERE session_id = %s AND id >= %s
                    ''',
                    (session_id, from_event_id),
                )
                conn.commit()
                return cursor.rowcount


@lru_cache
def get_chat_events_table() -> ChatEventsTable:
    return ChatEventsTable()
