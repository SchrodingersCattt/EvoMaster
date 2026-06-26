from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from src.services.sessions_service import ChatSessionsService


def test_list_user_run_statuses_filter_rechecks_redis_runtime_state():
    table = MagicMock()
    table.list_runtime_sessions.return_value = [
        {
            "session_id": "running-1",
            "user_id": "user-1",
            "project_id": 1,
            "status": "active",
            "session_title": "Running",
            "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        },
        {
            "session_id": "queued-1",
            "user_id": "user-1",
            "project_id": 1,
            "status": "waiting",
            "session_title": "",
            "first_message": '"Fallback title"',
            "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        },
        {
            "session_id": "stale-1",
            "user_id": "user-1",
            "project_id": 1,
            "status": "active",
            "session_title": "Stale",
            "updated_at": datetime(2026, 1, 3, tzinfo=timezone.utc),
        },
    ]

    registry = MagicMock()
    registry.get_session_run_owner.side_effect = lambda sid: {
        "running-1": "worker-a",
        "stale-1": "worker-dead",
    }.get(sid)
    registry.is_worker_alive.side_effect = lambda wid: wid == "worker-a"

    redis_dao = MagicMock()
    redis_dao.is_session_run_queued.side_effect = lambda sid: sid == "queued-1"

    with (
        patch("src.services.sessions_service.REDIS_URL", "redis://test"),
        patch(
            "src.services.sessions_service.get_worker_registry_service",
            return_value=registry,
        ),
        patch("src.services.sessions_service.get_redis_dao", return_value=redis_dao),
    ):
        result = ChatSessionsService(table).list_user_run_statuses(
            user_id=" user-1 ",
            page=1,
            page_size=20,
        )

    table.list_runtime_sessions.assert_called_once_with("user-1")
    assert result["redis_enabled"] is True
    assert result["total"] == 1
    item = result["items"][0]
    assert item["user_id"] == "user-1"
    assert item["running_count"] == 1
    assert item["queued_count"] == 1
    assert item["stale_count"] == 1
    assert item["running_sessions"][0]["session_id"] == "running-1"
    assert item["running_sessions"][0]["worker_id"] == "worker-a"
    assert item["queued_sessions"][0]["session_id"] == "queued-1"
    assert item["queued_sessions"][0]["title"] == "Fallback title"
    assert item["stale_sessions"][0]["session_id"] == "stale-1"


def test_list_user_run_statuses_groups_users_and_supports_filter():
    table = MagicMock()
    table.list_runtime_sessions.return_value = [
        {
            "session_id": "u2-running",
            "user_id": "user-2",
            "project_id": 2,
            "status": "active",
            "session_title": "User 2",
            "updated_at": datetime(2026, 1, 3, tzinfo=timezone.utc),
        },
        {
            "session_id": "u1-running",
            "user_id": "user-1",
            "project_id": 1,
            "status": "active",
            "session_title": "User 1",
            "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        },
        {
            "session_id": "u1-queued",
            "user_id": "user-1",
            "project_id": 1,
            "status": "waiting",
            "session_title": "User 1 queued",
            "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        },
    ]

    registry = MagicMock()
    registry.get_session_run_owner.side_effect = lambda sid: {
        "u1-running": "worker-a",
        "u2-running": "worker-b",
    }.get(sid)
    registry.is_worker_alive.side_effect = lambda wid: wid in {"worker-a", "worker-b"}

    redis_dao = MagicMock()
    redis_dao.is_session_run_queued.side_effect = lambda sid: sid == "u1-queued"

    with (
        patch("src.services.sessions_service.REDIS_URL", "redis://test"),
        patch(
            "src.services.sessions_service.get_worker_registry_service",
            return_value=registry,
        ),
        patch("src.services.sessions_service.get_redis_dao", return_value=redis_dao),
    ):
        result = ChatSessionsService(table).list_user_run_statuses(
            user_id=None,
            page=1,
            page_size=10,
        )

    table.list_runtime_sessions.assert_called_once_with(None)
    assert result["total"] == 2
    assert result["page"] == 1
    assert result["page_size"] == 10
    assert result["items"][0]["user_id"] == "user-2"
    assert result["items"][0]["running_count"] == 1
    assert result["items"][1]["user_id"] == "user-1"
    assert result["items"][1]["running_count"] == 1
    assert result["items"][1]["queued_count"] == 1


def test_list_user_run_statuses_normalizes_user_filter():
    table = MagicMock()
    table.list_runtime_sessions.return_value = []

    result = ChatSessionsService(table).list_user_run_statuses(
        user_id=" user-1 ",
        page=1,
        page_size=20,
    )

    table.list_runtime_sessions.assert_called_once_with("user-1")
    assert result["total"] == 0
