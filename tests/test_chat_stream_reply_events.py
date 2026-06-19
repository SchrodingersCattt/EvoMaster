from unittest.mock import MagicMock, patch


def test_publish_reply_event_publishes_redis() -> None:
    from src.services.stream_service import ChatStreamService

    service = ChatStreamService(
        sessions_service=MagicMock(),
        events_service=MagicMock(),
        deploy_state_service=MagicMock(),
    )
    fake_redis = MagicMock()

    payload = {
        "source": "User",
        "type": "interaction_reply",
        "kind": "ask_question",
        "request_id": "aq_1",
        "payload": {"answers": {}, "annotations": {}},
        "session_id": "sess-1",
    }

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=fake_redis),
    ):
        service.publish_reply_event("sess-1", payload)

    fake_redis.publish_stream_event.assert_called_once_with("sess-1", payload)


def test_send_stream_context_does_not_carry_unused_reply_queue() -> None:
    from dataclasses import fields

    from src.services.stream_service import SendStreamContext

    field_names = {field.name for field in fields(SendStreamContext)}

    assert "reply_queue" not in field_names
    assert "invocation_id" in field_names


def test_stream_service_does_not_expose_legacy_broadcast_reply_helper() -> None:
    from src.services.stream_service import ChatStreamService

    assert not hasattr(ChatStreamService, "broadcast_reply")
