from unittest.mock import MagicMock

import pytest

from src.apis import chat_api
from src.apis.chat_api import chat_stream
from src.models.chat import ChatSendRequest


def test_chat_api_has_no_agent_prompt_builder() -> None:
    assert not hasattr(chat_api, "_build_agent_prompt")


@pytest.mark.asyncio
async def test_chat_stream_sends_raw_user_text_to_stream_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat_api, "REDIS_URL", "redis://unit-test")
    monkeypatch.setattr(chat_api, "check_quota", MagicMock())

    request = MagicMock()
    request.url.path = "/api/v1/chat/sessions/sess-attachments/stream"
    request.headers = {}
    request.method = "POST"

    chat_svc = MagicMock()
    chat_svc.can_access_session.return_value = True

    stream_svc = MagicMock()
    stream_svc.prepare_send_message.return_value = MagicMock()
    stream_svc.generate_send_stream.return_value = iter(())

    events_svc = MagicMock()
    events_svc.get_session_user_query_events.side_effect = AssertionError(
        "chat_stream should not build attachment manifests"
    )
    events_svc.get_session_events.side_effect = AssertionError(
        "chat_stream should not load full session events for attachment manifest"
    )

    await chat_stream(
        request=request,
        session_id="sess-attachments",
        req=ChatSendRequest(content="new turn", mode="direct"),
        user_id=None,
        org_id=None,
        chat_svc=chat_svc,
        stream_svc=stream_svc,
        events_svc=events_svc,
    )

    events_svc.get_session_user_query_events.assert_not_called()
    events_svc.get_session_events.assert_not_called()
    stream_svc.generate_send_stream.assert_called_once()
    user_prompt = stream_svc.generate_send_stream.call_args.args[1]
    assert user_prompt == "new turn"
