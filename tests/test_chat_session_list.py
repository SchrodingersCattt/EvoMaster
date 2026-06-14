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
        "groups": [
            {
                "session_directory": "/share/a",
                "session_count": 1,
                "sessions": [
                    {
                        "id": "session-project-1",
                        "project_id": 42,
                        "status": "idle",
                        "history_length": 3,
                        "first_user_message": "hello",
                    }
                ],
                "has_more": False,
                "next_cursor": None,
            }
        ],
        "total_sessions": 1,
    }

    app.dependency_overrides[get_sessions_service] = lambda: mock_chat_svc
    try:
        with _test_client_without_real_lifespan() as client:
            response = client.get(
                "/api/v1/chat/sessions/list",
                params={"project_id": 42},
                headers={"X-User-Id": "test-user-1"},
            )

            assert response.status_code == 200, response.text
            data = response.json()
            assert data["data"]["total_sessions"] == 1
            assert data["data"]["groups"][0]["session_directory"] == "/share/a"
            assert data["data"]["groups"][0]["session_count"] == 1
            assert data["data"]["groups"][0]["has_more"] is False
            assert data["data"]["groups"][0]["sessions"][0]["id"] == "session-project-1"
            assert data["data"]["groups"][0]["sessions"][0]["project_id"] == 42
            mock_chat_svc.list_sessions_grouped_by_directory.assert_called_once_with(
                user_id="test-user-1",
                project_id=42,
                per_group_limit=10,
            )
    finally:
        app.dependency_overrides.pop(get_sessions_service, None)


def test_list_sessions_more_calls_service_with_cursor():
    mock_chat_svc = MagicMock()
    mock_chat_svc.list_sessions_more_in_directory.return_value = {
        "sessions": [
            {
                "id": "s2",
                "project_id": 42,
                "status": "idle",
                "history_length": 1,
                "first_user_message": "more",
            }
        ],
        "has_more": False,
        "next_cursor": None,
    }
    app.dependency_overrides[get_sessions_service] = lambda: mock_chat_svc
    try:
        with _test_client_without_real_lifespan() as client:
            response = client.get(
                "/api/v1/chat/sessions/list/more",
                params={
                    "project_id": 42,
                    "directory": "/share/a",
                    "cursor": "dGVzdA",
                    "limit": 10,
                },
                headers={"X-User-Id": "test-user-1"},
            )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["data"]["sessions"][0]["id"] == "s2"
        mock_chat_svc.list_sessions_more_in_directory.assert_called_once_with(
            user_id="test-user-1",
            project_id=42,
            directory="/share/a",
            limit=10,
            cursor_token="dGVzdA",
        )
    finally:
        app.dependency_overrides.pop(get_sessions_service, None)


def test_delete_sessions_by_directory_normalizes_and_calls_service():
    mock_chat_svc = MagicMock()
    mock_chat_svc.delete_sessions_by_directory.return_value = {
        "deleted_count": 3,
        "blocked_count": 0,
        "blocked_statuses": [],
    }
    app.dependency_overrides[get_sessions_service] = lambda: mock_chat_svc
    try:
        with _test_client_without_real_lifespan() as client:
            response = client.delete(
                "/api/v1/chat/sessions/by-directory",
                params={
                    "project_id": 42,
                    "directory": " /share/foo/../bar/ ",
                },
                headers={"X-User-Id": "test-user-1"},
            )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["deleted_count"] == 3
        mock_chat_svc.delete_sessions_by_directory.assert_called_once_with(
            user_id="test-user-1",
            project_id=42,
            directory="/share/bar",
        )
    finally:
        app.dependency_overrides.pop(get_sessions_service, None)


def test_delete_sessions_by_directory_supports_unset_directory():
    mock_chat_svc = MagicMock()
    mock_chat_svc.delete_sessions_by_directory.return_value = {
        "deleted_count": 1,
        "blocked_count": 0,
        "blocked_statuses": [],
    }
    app.dependency_overrides[get_sessions_service] = lambda: mock_chat_svc
    try:
        with _test_client_without_real_lifespan() as client:
            response = client.delete(
                "/api/v1/chat/sessions/by-directory",
                params={
                    "project_id": 42,
                    "unset_directory": "true",
                },
                headers={"X-User-Id": "test-user-1"},
            )

        assert response.status_code == 200, response.text
        mock_chat_svc.delete_sessions_by_directory.assert_called_once_with(
            user_id="test-user-1",
            project_id=42,
            directory=None,
        )
    finally:
        app.dependency_overrides.pop(get_sessions_service, None)


def test_delete_sessions_by_directory_returns_conflict_when_blocked():
    mock_chat_svc = MagicMock()
    mock_chat_svc.delete_sessions_by_directory.return_value = {
        "deleted_count": 0,
        "blocked_count": 2,
        "blocked_statuses": ["active", "waiting"],
    }
    app.dependency_overrides[get_sessions_service] = lambda: mock_chat_svc
    try:
        with _test_client_without_real_lifespan() as client:
            response = client.delete(
                "/api/v1/chat/sessions/by-directory",
                params={
                    "project_id": 42,
                    "directory": "/share/a",
                },
                headers={"X-User-Id": "test-user-1"},
            )

        assert response.status_code == 409, response.text
        body = response.json()
        assert body["data"]["blocked_count"] == 2
        assert body["data"]["blocked_statuses"] == ["active", "waiting"]
    finally:
        app.dependency_overrides.pop(get_sessions_service, None)


def test_delete_session_returns_conflict_when_running():
    mock_chat_svc = MagicMock()
    mock_chat_svc.get_session.return_value = {
        "session_id": "s-active",
        "user_id": "test-user-1",
        "status": "active",
    }
    mock_chat_svc.reconcile_waiting_status.return_value = "active"
    app.dependency_overrides[get_sessions_service] = lambda: mock_chat_svc
    try:
        with _test_client_without_real_lifespan() as client:
            response = client.delete(
                "/api/v1/chat/sessions/s-active",
                headers={"X-User-Id": "test-user-1"},
            )

        assert response.status_code == 409, response.text
        mock_chat_svc.delete_session.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_sessions_service, None)


def test_list_sessions_requires_project_id():
    """缺少 project_id 时应 422；须 mock 会话服务，避免部分环境下依赖解析顺序触发真实 DB。"""
    mock_chat_svc = MagicMock()
    app.dependency_overrides[get_sessions_service] = lambda: mock_chat_svc
    try:
        with _test_client_without_real_lifespan() as client:
            response = client.get(
                "/api/v1/chat/sessions/list",
                params={"per_group_limit": 100},
                headers={"X-User-Id": "test-user-1"},
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
        "src.services.sessions_service.get_worker_registry_service",
        return_value=MagicMock(),
    ):
        from src.services.sessions_service import ChatSessionsService

        service = ChatSessionsService(mock_table)
        sessions, total = service.list_sessions(
            user_id="test-user-2",
            limit=10,
            offset=5,
            project_id=99,
        )

    assert sessions == []
    assert total == 0
    mock_table.list_sessions.assert_called_once_with(
        user_id="test-user-2",
        limit=10,
        offset=5,
        project_id=99,
    )
    mock_table.count_sessions_by_user.assert_called_once_with(
        "test-user-2",
        project_id=99,
    )


def test_sessions_service_delete_directory_rejects_blocked_status():
    mock_table = MagicMock()
    mock_table.list_session_delete_candidates_by_directory.return_value = [
        {"session_id": "s-idle", "status": "idle"},
        {"session_id": "s-active", "status": "active"},
    ]

    with patch(
        "src.services.sessions_service.get_worker_registry_service",
        return_value=MagicMock(),
    ):
        from src.services.sessions_service import ChatSessionsService

        service = ChatSessionsService(mock_table)
        result = service.delete_sessions_by_directory(
            user_id="test-user-2",
            project_id=99,
            directory="/share/a",
        )

    assert result == {
        "deleted_count": 0,
        "blocked_count": 1,
        "blocked_statuses": ["active"],
    }
    mock_table.soft_delete_sessions_by_directory_if_unblocked.assert_not_called()


def test_sessions_service_delete_directory_soft_deletes_all_candidates():
    mock_table = MagicMock()
    mock_table.list_session_delete_candidates_by_directory.return_value = [
        {"session_id": "s-1", "status": "idle"},
        {"session_id": "s-2", "status": "failed"},
    ]
    mock_table.soft_delete_sessions_by_directory_if_unblocked.return_value = {
        "session_ids": ["s-1", "s-2"],
        "deleted_count": 2,
        "blocked_count": 0,
        "blocked_statuses": [],
    }
    registry = MagicMock()

    with patch(
        "src.services.sessions_service.get_worker_registry_service",
        return_value=registry,
    ):
        from src.services.sessions_service import ChatSessionsService

        service = ChatSessionsService(mock_table)
        result = service.delete_sessions_by_directory(
            user_id="test-user-2",
            project_id=99,
            directory=None,
        )

    assert result == {
        "deleted_count": 2,
        "blocked_count": 0,
        "blocked_statuses": [],
    }
    mock_table.soft_delete_sessions_by_directory_if_unblocked.assert_called_once_with(
        "test-user-2",
        99,
        directory=None,
        blocked_statuses=("active", "waiting"),
    )
    registry.delete_session_run_owner.assert_any_call("s-1")
    registry.delete_session_run_owner.assert_any_call("s-2")


def test_sessions_service_delete_directory_honors_transaction_block():
    mock_table = MagicMock()
    mock_table.list_session_delete_candidates_by_directory.return_value = [
        {"session_id": "s-1", "status": "idle"},
        {"session_id": "s-2", "status": "idle"},
    ]
    mock_table.soft_delete_sessions_by_directory_if_unblocked.return_value = {
        "session_ids": [],
        "deleted_count": 0,
        "blocked_count": 1,
        "blocked_statuses": ["waiting"],
    }
    registry = MagicMock()

    with patch(
        "src.services.sessions_service.get_worker_registry_service",
        return_value=registry,
    ):
        from src.services.sessions_service import ChatSessionsService

        service = ChatSessionsService(mock_table)
        result = service.delete_sessions_by_directory(
            user_id="test-user-2",
            project_id=99,
            directory="/share/a",
        )

    assert result == {
        "deleted_count": 0,
        "blocked_count": 1,
        "blocked_statuses": ["waiting"],
    }
    registry.delete_session_run_owner.assert_not_called()


def test_sessions_service_denies_access_to_soft_deleted_session():
    mock_table = MagicMock()
    mock_table.get_session.return_value = {
        "session_id": "s-deleted",
        "user_id": "test-user-2",
        "deleted_at": object(),
    }

    from src.services.sessions_service import ChatSessionsService

    service = ChatSessionsService(mock_table)

    assert service.can_access_session("s-deleted", "test-user-2") is False
    mock_table.get_session.assert_called_once_with(
        "s-deleted",
        include_deleted=True,
    )
