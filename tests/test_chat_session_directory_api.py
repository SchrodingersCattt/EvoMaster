from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app import app
from src.services.sessions_service import get_sessions_service


def _install_sessions_service(mock_svc):
    app.dependency_overrides[get_sessions_service] = lambda: mock_svc


def _clear_overrides():
    app.dependency_overrides.pop(get_sessions_service, None)


def test_put_session_directory_normalizes_share_path_before_storage():
    mock_svc = MagicMock()
    mock_svc.set_session_directory.return_value = True
    mock_svc.get_session.return_value = {"session_directory": "/share/bar"}
    _install_sessions_service(mock_svc)

    try:
        client = TestClient(app)
        response = client.put(
            "/api/v1/chat/sessions/sess-1/session-directory",
            json={"directory": " /share/foo/../bar/ "},
            headers={"X-User-Id": "user-1"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    assert response.json()["data"]["directory"] == "/share/bar"
    mock_svc.set_session_directory.assert_called_once_with(
        "sess-1",
        "/share/bar",
        "user-1",
    )


def test_put_session_directory_rejects_outside_share_without_writing():
    mock_svc = MagicMock()
    _install_sessions_service(mock_svc)

    try:
        client = TestClient(app)
        response = client.put(
            "/api/v1/chat/sessions/sess-1/session-directory",
            json={"directory": "/tmp/foo"},
            headers={"X-User-Id": "user-1"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["data"]["error_code"] == "directory_outside_share"
    mock_svc.set_session_directory.assert_not_called()


def test_put_session_directory_blank_clears_storage_value():
    mock_svc = MagicMock()
    mock_svc.set_session_directory.return_value = True
    mock_svc.get_session.return_value = {"session_directory": None}
    _install_sessions_service(mock_svc)

    try:
        client = TestClient(app)
        response = client.put(
            "/api/v1/chat/sessions/sess-1/session-directory",
            json={"directory": "   "},
            headers={"X-User-Id": "user-1"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    assert response.json()["data"]["directory"] is None
    mock_svc.set_session_directory.assert_called_once_with(
        "sess-1",
        None,
        "user-1",
    )
