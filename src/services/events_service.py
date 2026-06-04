"""会话事件服务：历史事件追加、按会话查询。"""

import logging
from collections.abc import Iterable
from functools import lru_cache

from matmaster.utils.event_source import normalize_event_source
from src.dao.chat_events_table import ChatEventsTable, get_chat_events_table
from src.services.sessions_service import ChatSessionsService, get_sessions_service

logger = logging.getLogger(__name__)


class ChatEventsService:
    """会话事件服务：追加历史事件、获取会话事件列表。"""

    def __init__(
        self,
        events_table: ChatEventsTable | None = None,
        sessions_service: ChatSessionsService | None = None,
    ):
        self.table = events_table or get_chat_events_table()
        self._sessions_service = sessions_service or get_sessions_service()

    def add_history_event(
        self,
        session_id: str,
        payload: dict,
        user_id: str | None = None,
    ) -> None:
        """向会话历史追加一条事件（仅持久化到 DB）。user_id 可为空（如分享后匿名访问）。"""
        self._sessions_service.ensure_session(session_id, user_id=user_id)
        if not self.table:
            return
        source = normalize_event_source(payload.get('source', 'System'))
        event_type = payload.get('type', 'unknown')
        content = payload.get('content', '')
        # User/query 带附件元数据时存成 { content, files?, images?, workspace_paths?, session_directory?, session_directory_source? }，
        # 以便读回时前端分开展示，agent 历史恢复也能拿到结构化图片输入和目录信息。
        query_metadata_keys = (
            'files',
            'images',
            'workspace_paths',
            'session_directory',
            'session_directory_source',
            'requested_llm',
            'requested_model',
        )
        if (
            source == 'User'
            and event_type == 'query'
            and any(payload.get(key) for key in query_metadata_keys)
        ):
            content = {'content': content}
            if payload.get('files'):
                content['files'] = list(payload['files'])
            if payload.get('images'):
                content['images'] = list(payload['images'])
            if payload.get('workspace_paths'):
                content['workspace_paths'] = list(payload['workspace_paths'])
            if payload.get('session_directory'):
                content['session_directory'] = payload['session_directory']
            if payload.get('session_directory_source'):
                content['session_directory_source'] = payload[
                    'session_directory_source'
                ]
            if payload.get('requested_llm'):
                content['requested_llm'] = payload['requested_llm']
            if payload.get('requested_model'):
                content['requested_model'] = payload['requested_model']
        task_id = payload.get('task_id')
        invocation_id = payload.get('invocation_id')
        self.table.add_event(
            session_id,
            source,
            event_type,
            content,
            task_id=task_id,
            invocation_id=invocation_id,
        )

    def get_session_events(
        self,
        session_id: str,
        include_spawn: bool = False,
        exclude_types: Iterable[str] | None = None,
    ) -> list:
        """返回某会话的历史消息列表（从数据库读取）。默认仅父级事件；include_spawn 含子 agent 行。

        exclude_types：在 SQL 层过滤掉这些事件类型，避免读取/解析注定会被丢弃的大体积行（回放路径用）。
        """
        return self.table.get_session_events(
            session_id, include_spawn=include_spawn, exclude_types=exclude_types
        )

    def get_session_user_query_events(self, session_id: str) -> list:
        """返回某会话的父级 User/query 历史事件（从数据库读取）。"""
        return self.table.get_session_user_query_events(session_id)

    def get_latest_scope_event_id(
        self,
        session_id: str,
        spawn_id: str | None = None,
    ) -> int:
        if not self.table:
            return 0
        return self.table.get_latest_scope_event_id(session_id, spawn_id)

    def get_last_user_query(self, session_id: str):
        """
        获取该会话最后一次用户输入（User/query），用于部署中断后提示重跑。
        返回 None 或 dict：content, files, images, workspace_paths, mode, session_directory, session_directory_source, task_id。
        """
        return self.table.get_last_user_query(session_id)

    def get_last_user_query_event(self, session_id: str) -> dict | None:
        """返回最后一条 User/query 事件的完整行（含 id），用于 replace_last_turn。"""
        return self.table.get_last_user_query_event(session_id)

    def delete_events_from_id(self, session_id: str, from_event_id: int) -> int:
        """物理删除 session 中 id >= from_event_id 的所有事件。"""
        return self.table.delete_events_from_id(session_id, from_event_id)


@lru_cache
def get_events_service() -> ChatEventsService:
    return ChatEventsService(
        events_table=get_chat_events_table(),
        sessions_service=get_sessions_service(),
    )
