from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from src.services.sessions_service import get_sessions_service


def test_list_sessions_filters_by_project_id():
    mock_chat_svc = MagicMock()
    mock_chat_svc.list_sessions.return_value = (
        [
            {
                'id': 'session-project-1',
                'status': 'idle',
                'history_length': 3,
                'first_user_message': 'hello',
            }
        ],
        1,
    )

    app.dependency_overrides[get_sessions_service] = lambda: mock_chat_svc
    try:
        client = TestClient(app)
        response = client.get(
            '/api/v1/chat/sessions/list',
            params={'limit': 20, 'offset': 0, 'project_id': 42},
            headers={'X-User-Id': 'test-user-1'},
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data['data']['total'] == 1
        assert data['data']['has_more'] is False
        assert data['data']['sessions'][0]['id'] == 'session-project-1'
        mock_chat_svc.list_sessions.assert_called_once_with(
            user_id='test-user-1',
            limit=20,
            offset=0,
            project_id=42,
        )
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
