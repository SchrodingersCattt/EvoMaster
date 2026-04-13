from unittest.mock import MagicMock, patch

from src.services.sessions_service import ChatSessionsService


def test_can_access_session_admin_read_allows_when_allowlisted():
    mock_table = MagicMock()
    mock_table.get_session.return_value = {
        'session_id': 's1',
        'user_id': 'owner',
        'is_shared': 0,
    }
    with patch(
        'src.services.sessions_service.get_worker_registry_service',
        return_value=MagicMock(),
    ):
        svc = ChatSessionsService(mock_table)
    with patch(
        'src.services.sessions_service.is_user_in_admin_allowlist',
        return_value=True,
    ):
        ok = svc.can_access_session(
            's1',
            'admin-user',
            allow_admin_read=True,
        )
    assert ok is True


def test_can_access_session_admin_read_denied_when_not_allowlisted():
    mock_table = MagicMock()
    mock_table.get_session.return_value = {
        'session_id': 's1',
        'user_id': 'owner',
        'is_shared': 0,
    }
    with patch(
        'src.services.sessions_service.get_worker_registry_service',
        return_value=MagicMock(),
    ):
        svc = ChatSessionsService(mock_table)
    with patch(
        'src.services.sessions_service.is_user_in_admin_allowlist',
        return_value=False,
    ):
        ok = svc.can_access_session(
            's1',
            'other',
            allow_admin_read=True,
        )
    assert ok is False


def test_can_access_session_default_no_admin_branch():
    mock_table = MagicMock()
    mock_table.get_session.return_value = {
        'session_id': 's1',
        'user_id': 'owner',
        'is_shared': 0,
    }
    with patch(
        'src.services.sessions_service.get_worker_registry_service',
        return_value=MagicMock(),
    ):
        svc = ChatSessionsService(mock_table)
    with patch(
        'src.services.sessions_service.is_user_in_admin_allowlist',
        return_value=True,
    ) as m:
        ok = svc.can_access_session('s1', 'admin-user', allow_admin_read=False)
    assert ok is False
    m.assert_not_called()
