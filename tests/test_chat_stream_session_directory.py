from unittest.mock import MagicMock, patch

import pytest

from src.models.chat import ChatSendRequest
from src.services.stream_service import ChatStreamService


def _service(session_directory=None):
    sessions_service = MagicMock()
    sessions_service.get_session.return_value = {
        "session_directory": session_directory,
    }
    sessions_service.try_acquire_session_run.return_value = (True, None)
    events_service = MagicMock()
    deploy_state_service = MagicMock()
    return (
        ChatStreamService(
            sessions_service=sessions_service,
            events_service=events_service,
            deploy_state_service=deploy_state_service,
        ),
        sessions_service,
        events_service,
    )


def test_prepare_send_message_uses_request_directory_and_marks_bohrium_required():
    service, sessions_service, events_service = _service("/share/default")
    req = ChatSendRequest(content="run", directory="/share/request/../case")

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=MagicMock()),
    ):
        ctx = service.prepare_send_message("sess-1", req, user_id="user-1")

    assert ctx is not None
    assert ctx.job["workspace"] == "/share/case"
    assert "remote_workdir" not in ctx.job
    assert "session_directory_source" not in ctx.job
    assert ctx.job["bohrium_required"] is True
    assert ctx.user_msg["session_directory"] == "/share/case"
    assert ctx.user_msg["session_directory_source"] == "request"
    events_service.add_history_event.assert_called_once()
    stored = events_service.add_history_event.call_args.args[1]
    assert stored["session_directory"] == "/share/case"
    assert stored["session_directory_source"] == "request"
    sessions_service.try_acquire_session_run.assert_called_once_with("sess-1")


def test_prepare_send_message_blank_request_falls_through_to_session_directory():
    service, _sessions_service, _events_service = _service("/share/default")
    req = ChatSendRequest(content="run", directory="   ")

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=MagicMock()),
    ):
        ctx = service.prepare_send_message("sess-1", req, user_id="user-1")

    assert ctx is not None
    assert ctx.job["workspace"] == "/share/default"
    assert "remote_workdir" not in ctx.job
    assert "session_directory_source" not in ctx.job
    assert ctx.user_msg["session_directory"] == "/share/default"
    assert ctx.user_msg["session_directory_source"] == "session"


def test_prepare_send_message_without_directory_keeps_existing_no_bohrium_behavior():
    service, _sessions_service, events_service = _service(None)
    req = ChatSendRequest(content="run")

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=MagicMock()),
    ):
        ctx = service.prepare_send_message("sess-1", req, user_id="user-1")

    assert ctx is not None
    assert ctx.job["workspace"] is None
    assert "remote_workdir" not in ctx.job
    assert "session_directory_source" not in ctx.job
    assert ctx.job["bohrium_required"] is False
    stored = events_service.add_history_event.call_args.args[1]
    assert "session_directory" not in stored
    assert "session_directory_source" not in stored


def test_prepare_send_message_invalid_request_directory_does_not_acquire_run():
    service, sessions_service, events_service = _service("/share/default")
    req = ChatSendRequest(content="run", directory="/tmp/bad")

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=MagicMock()),
    ):
        with pytest.raises(Exception) as exc:
            service.prepare_send_message("sess-1", req, user_id="user-1")

    assert exc.value.error_code == "directory_outside_share"
    sessions_service.try_acquire_session_run.assert_not_called()
    events_service.add_history_event.assert_not_called()


def test_prepare_send_message_invalid_session_directory_does_not_acquire_run():
    service, sessions_service, events_service = _service("/tmp/bad-default")
    req = ChatSendRequest(content="run")

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=MagicMock()),
    ):
        with pytest.raises(Exception) as exc:
            service.prepare_send_message("sess-1", req, user_id="user-1")

    assert exc.value.error_code == "session_directory_invalid"
    sessions_service.try_acquire_session_run.assert_not_called()
    events_service.add_history_event.assert_not_called()
