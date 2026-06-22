from unittest.mock import MagicMock, patch


def test_prepare_send_message_rejects_unknown_platform_model():
    from src.models.chat import ChatSendRequest
    from src.services.llm_profile_validation import InvalidModelProfileError
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
        content='run',
        model='matmaster/qwen3.6-max-preview',
    )

    with (
        patch('src.services.stream_service.REDIS_URL', 'redis://test'),
        patch('src.services.stream_service.get_redis_dao', return_value=MagicMock()),
    ):
        try:
            service.prepare_send_message('sess-1', req, user_id='user-1')
        except InvalidModelProfileError as exc:
            assert exc.profile_key == 'matmaster/qwen3.6-max-preview'
        else:
            raise AssertionError('expected InvalidModelProfileError')

    sessions_service.try_acquire_session_run.assert_not_called()
    events_service.add_history_event.assert_not_called()
