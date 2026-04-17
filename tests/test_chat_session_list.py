from contextlib import asynccontextmanager, contextmanager
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from src.services.sessions_service import get_sessions_service


@asynccontextmanager
async def _noop_lifespan(_app):
    """避免 TestClient 触发真实 lifespan（连 DB / Redis）。"""
    yield


@contextmanager
def _test_client_without_real_lifespan():
    """Starlette TestClient 会跑 ASGI lifespan；无本机 MySQL 时需跳过真实启动逻辑。"""
    router = app.router
    saved = router.lifespan_context
    router.lifespan_context = _noop_lifespan
    try:
        yield TestClient(app)
    finally:
        router.lifespan_context = saved


def test_list_sessions_returns_grouped_by_project():
    mock_chat_svc = MagicMock()
    mock_chat_svc.list_sessions_grouped_by_directory.return_value = {
        'groups': [
            {
                'session_directory': '/share/a',
                'sessions': [
                    {
                        'id': 'session-project-1',
                        'project_id': 42,
                        'status': 'idle',
                        'history_length': 3,
                        'first_user_message': 'hello',
                    }
                ],
            }
        ],
        'total_sessions': 1,
        'loaded_sessions': 1,
        'truncated': False,
    }

    app.dependency_overrides[get_sessions_service] = lambda: mock_chat_svc
    try:
        with _test_client_without_real_lifespan() as client:
            response = client.get(
                '/api/v1/chat/sessions/list',
                params={'project_id': 42},
                headers={'X-User-Id': 'test-user-1'},
            )

            assert response.status_code == 200, response.text
            data = response.json()
            assert data['data']['total_sessions'] == 1
            assert data['data']['loaded_sessions'] == 1
            assert data['data']['truncated'] is False
            assert data['data']['groups'][0]['session_directory'] == '/share/a'
            assert data['data']['groups'][0]['sessions'][0]['id'] == 'session-project-1'
            assert data['data']['groups'][0]['sessions'][0]['project_id'] == 42
            mock_chat_svc.list_sessions_grouped_by_directory.assert_called_once_with(
                user_id='test-user-1',
                project_id=42,
                max_sessions=500,
            )
    finally:
        app.dependency_overrides.pop(get_sessions_service, None)


def test_list_sessions_requires_project_id():
    """缺少 project_id 时应 422；须 mock 会话服务，避免部分环境下依赖解析顺序触发真实 DB。"""
    mock_chat_svc = MagicMock()
    app.dependency_overrides[get_sessions_service] = lambda: mock_chat_svc
    try:
        with _test_client_without_real_lifespan() as client:
            response = client.get(
                '/api/v1/chat/sessions/list',
                params={'max_sessions': 100},
                headers={'X-User-Id': 'test-user-1'},
            )
        assert response.status_code == 422
        mock_chat_svc.list_sessions_grouped_by_directory.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_sessions_service, None)


def test_sessions_service_passes_project_id_to_table():
    mock_table = MagicMock()
    mock_table.list_sessions.return_value = []
    mock_table.count_sessions_by_user.return_value = 0

    with patch(
        'src.services.sessions_service.get_worker_registry_service',
        return_value=MagicMock(),
    ):
        from src.services.sessions_service import ChatSessionsService

        service = ChatSessionsService(mock_table)
        sessions, total = service.list_sessions(
            user_id='test-user-2',
            limit=10,
            offset=5,
            project_id=99,
        )

    assert sessions == []
    assert total == 0
    mock_table.list_sessions.assert_called_once_with(
        user_id='test-user-2',
        limit=10,
        offset=5,
        project_id=99,
    )
    mock_table.count_sessions_by_user.assert_called_once_with(
        'test-user-2',
        project_id=99,
    )
