"""会话事件服务：历史事件追加、按会话查询。"""

import logging
from functools import lru_cache

from src.dao.chat_events_table import ChatEventsTable, get_chat_events_table
from src.services.sessions_service import (
    SESSIONS,
    ChatSessionsService,
    get_sessions_service,
)

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
        """向会话历史追加一条事件。user_id 可为空（如分享后匿名访问）。"""
        self._sessions_service.ensure_session(session_id, user_id=user_id)
        SESSIONS[session_id]['history'].append(payload)
        if not self.table:
            return
        source = payload.get('source', 'System')
        event_type = payload.get('type', 'unknown')
        content = payload.get('content', '')
        # User/query 带 files 时存成 { content, files } 以便读回时前端 content/files 分开展示
        if source == 'User' and event_type == 'query' and payload.get('files'):
            content = {'content': content, 'files': list(payload['files'])}
        task_id = payload.get('task_id')
        self.table.add_event(session_id, source, event_type, content, task_id)

    def get_session_events(self, session_id: str) -> list:
        """返回某会话的历史消息列表（从数据库读取）。"""
        return self.table.get_session_events(session_id)


@lru_cache
def get_events_service() -> ChatEventsService:
    return ChatEventsService(
        events_table=get_chat_events_table(),
        sessions_service=get_sessions_service(),
    )
