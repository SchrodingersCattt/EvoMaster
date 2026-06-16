"""ChatSessionsService 存活判定：只把确实还有在途 run 算作存活，过滤部署残留。"""

from unittest.mock import MagicMock, patch

from src.services.sessions_service import ChatSessionsService


def _make_service(table=None):
    return ChatSessionsService(table or MagicMock())


def test_is_session_run_live_true_when_queued():
    svc = _make_service()
    redis = MagicMock()
    redis.is_session_run_queued.return_value = True
    with (
        patch("src.services.sessions_service.REDIS_URL", "redis://test"),
        patch("src.services.sessions_service.get_redis_dao", return_value=redis),
    ):
        assert svc.is_session_run_live("s1") is True


def test_list_live_run_session_ids_filters_stale():
    table = MagicMock()
    table.list_session_ids_by_status.return_value = ["live", "stale"]
    svc = _make_service(table)
    with patch.object(
        svc, "is_session_run_live", side_effect=lambda sid: sid == "live"
    ):
        result = svc.list_live_run_session_ids("user-1")
    table.list_session_ids_by_status.assert_called_once_with(
        "user-1", ["waiting", "active"]
    )
    assert result == ["live"]


def test_is_session_run_live_false_when_stale():
    svc = _make_service()
    redis = MagicMock()
    redis.is_session_run_queued.return_value = False
    registry = MagicMock()
    registry.get_session_run_owner.return_value = None
    with (
        patch("src.services.sessions_service.REDIS_URL", "redis://test"),
        patch("src.services.sessions_service.get_redis_dao", return_value=redis),
        patch(
            "src.services.sessions_service.get_worker_registry_service",
            return_value=registry,
        ),
    ):
        assert svc.is_session_run_live("s1") is False


def test_is_session_run_live_true_when_owner_alive_on_another_pod():
    svc = _make_service()
    redis = MagicMock()
    redis.is_session_run_queued.return_value = False
    registry = MagicMock()
    registry.get_session_run_owner.return_value = "worker-OTHER"
    registry.is_worker_alive.return_value = True
    with (
        patch("src.services.sessions_service.REDIS_URL", "redis://test"),
        patch("src.services.sessions_service.get_redis_dao", return_value=redis),
        patch(
            "src.services.sessions_service.get_worker_registry_service",
            return_value=registry,
        ),
        patch(
            "src.services.sessions_service.get_worker_id", return_value="worker-SELF"
        ),
    ):
        assert svc.is_session_run_live("s1") is True
