import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.models.chat import ChatSendRequest
from src.services.stream_service import ChatStreamService, SendStreamContext


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
            agent_run_service=MagicMock(),
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
    assert ctx.remote_workdir == "/share/case"
    assert ctx.session_directory_source == "request"
    assert ctx.bohrium_required is True
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
    assert ctx.remote_workdir == "/share/default"
    assert ctx.session_directory_source == "session"
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
    assert ctx.remote_workdir is None
    assert ctx.session_directory_source == "none"
    assert ctx.bohrium_required is False
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


@pytest.mark.asyncio
async def test_generate_send_stream_enqueues_remote_workdir_and_source():
    service = ChatStreamService(
        sessions_service=MagicMock(
            get_session_status_payload=MagicMock(
                return_value={
                    "source": "System",
                    "type": "status",
                    "content": "",
                    "session_id": "sess-1",
                }
            )
        ),
        events_service=MagicMock(get_session_events=MagicMock(return_value=[])),
        agent_run_service=MagicMock(),
        deploy_state_service=MagicMock(),
    )
    ctx = SendStreamContext(
        task_id="task-1",
        invocation_id="inv-1",
        mode="direct",
        user_msg={"source": "User", "type": "query", "content": "run"},
        request_event_queue=asyncio.Queue(),
        remote_workdir="/share/case",
        session_directory_source="request",
        bohrium_required=True,
    )
    fake_redis = MagicMock()
    fake_redis.create_client.return_value = None
    fake_redis.set_session_run_queued.return_value = True
    fake_redis.llen_agent_run_queue.return_value = 0
    fake_redis.lpush_agent_run_job.return_value = True

    async def _stream_closed_immediately(awaitable, timeout):
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        return {
            "source": "System",
            "type": "stream_closed",
            "content": "",
            "session_id": "sess-1",
        }

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=fake_redis),
        patch("src.services.stream_service.notify_post_async"),
        patch(
            "src.services.stream_service.asyncio.wait_for",
            side_effect=_stream_closed_immediately,
        ),
    ):
        gen = service.generate_send_stream("sess-1", "run", ctx)
        await gen.__anext__()
        await gen.__anext__()
        await gen.__anext__()
        await gen.aclose()

    pushed_job = fake_redis.lpush_agent_run_job.call_args.args[0]
    assert pushed_job["remote_workdir"] == "/share/case"
    assert pushed_job["session_directory_source"] == "request"
    assert pushed_job["bohrium_required"] is True
