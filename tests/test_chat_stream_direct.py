"""Stream 接口测试：仅 Worker 队列模式。无 REDIS_URL 时发送返回 503；有 Redis 时验证入队与 SSE 流（可选）。"""

import uuid
from unittest.mock import MagicMock, patch

# 测试中屏蔽 DB：任何真实 BaseTable 触发的连接直接报错（应通过 get_*_table mock 避免走到这里）
_DB_DISABLED_ERROR = RuntimeError('DB disabled in test (use mock tables only)')


class _NoDbConnection:
    """占位 context manager：测试中禁止真实 DB 连接。"""

    def __enter__(self):
        raise _DB_DISABLED_ERROR

    def __exit__(self, *args):
        pass


def _mock_sessions_table():
    t = MagicMock()
    t.get_session.return_value = None
    t.create_session.return_value = None
    t.set_session_status.return_value = (
        True  # try_acquire_session_run 需其返回 True 才视为占用成功
    )
    t.set_session_last_task.return_value = None
    t.list_sessions.return_value = []
    t.count_sessions_by_user.return_value = 0
    t.count_active_sessions.return_value = 0
    t.reset_all_active_to_idle.return_value = 0
    t.set_share_status.return_value = False
    t.delete_session.return_value = False
    t.get_session.return_value = None
    return t


def _mock_events_table():
    t = MagicMock()
    t.get_session_events.return_value = []
    t.add_event.return_value = None
    return t


async def _check_quota_noop(user_id: str) -> int:
    return 10


def test_chat_stream_returns_503_when_redis_url_missing():
    """无 REDIS_URL 时 POST /stream 返回 503（仅 Worker 队列模式，发送需 Redis）。"""
    mock_sessions = _mock_sessions_table()
    mock_events = _mock_events_table()

    patches = [
        patch('src.apis.chat_api.REDIS_URL', None),
        patch(
            'src.base.base_table.BaseTable.get_connection',
            side_effect=lambda self: _NoDbConnection(),
        ),
        patch(
            'src.services.sessions_service.get_chat_sessions_table',
            return_value=mock_sessions,
        ),
        patch(
            'src.services.events_service.get_chat_events_table',
            return_value=mock_events,
        ),
        patch(
            'src.dao.chat_sessions_table.get_chat_sessions_table',
            return_value=mock_sessions,
        ),
        patch(
            'src.dao.chat_events_table.get_chat_events_table',
            return_value=mock_events,
        ),
        patch('src.apis.chat_api.check_quota', side_effect=_check_quota_noop),
    ]

    for p in patches:
        p.start()

    try:
        from src.services.events_service import get_events_service
        from src.services.sessions_service import get_sessions_service
        from src.services.stream_service import get_stream_service

        get_sessions_service.cache_clear()
        get_events_service.cache_clear()
        get_stream_service.cache_clear()

        from fastapi.testclient import TestClient

        from app import app

        client = TestClient(app)
        session_id = f'test-stream-503-{uuid.uuid4().hex[:12]}'
        url = f"/api/v1/chat/sessions/{session_id}/stream"
        headers = {'X-User-Id': 'test-user-3656033'}
        body = {'content': 'hello', 'mode': 'direct'}

        response = client.post(url, json=body, headers=headers)
        assert response.status_code == 503, response.text
        data = response.json()
        assert '队列' in data.get('msg', '') or 'REDIS' in data.get('msg', '')
    finally:
        for p in patches:
            p.stop()
