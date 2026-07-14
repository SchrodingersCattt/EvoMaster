from unittest.mock import MagicMock, patch


def test_prepare_send_message_passes_bohrium_node_sku_id_to_job():
    from src.models.chat import ChatSendRequest
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.get_session.return_value = {"session_directory": None}
    sessions_service.try_acquire_session_run.return_value = (True, None)
    events_service = MagicMock()
    events_service.get_latest_scope_event_id.return_value = 0
    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=MagicMock(),
    )
    req = ChatSendRequest(
        content="run",
        bohrium_node_sku_id=12345,
        bohrium_node_lifecycle_policy="idle_timeout",
        bohrium_node_idle_timeout_seconds=1800,
    )

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=MagicMock()),
    ):
        ctx = service.prepare_send_message("sess-1", req, user_id="user-1")

    assert ctx is not None
    assert ctx.job["bohrium_node_sku_id"] == 12345
    assert ctx.job["bohrium_node_lifecycle_policy"] == "idle_timeout"
    assert ctx.job["bohrium_node_idle_timeout_seconds"] == 1800


def test_prepare_send_message_defaults_legacy_requests_to_run_end():
    from src.models.chat import ChatSendRequest
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.get_session.return_value = {"session_directory": None}
    sessions_service.try_acquire_session_run.return_value = (True, None)
    events_service = MagicMock()
    events_service.get_latest_scope_event_id.return_value = 0
    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=MagicMock(),
    )

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=MagicMock()),
    ):
        ctx = service.prepare_send_message(
            "sess-1", ChatSendRequest(content="run"), user_id="user-1"
        )

    assert ctx.job["bohrium_node_lifecycle_policy"] == "run_end"
    assert ctx.job["bohrium_node_idle_timeout_seconds"] is None
