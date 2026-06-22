from unittest.mock import MagicMock

import pytest

from src.services import feishu_inbound_service
from src.services.user_runtime_preference_service import UserRuntimePreference


@pytest.mark.asyncio
async def test_feishu_passes_submit_confirmation_preference_to_chat_request(
    monkeypatch,
):
    captured: dict = {}

    class FakeQuotaStatus:
        is_exhausted = False

    class FakeStreamService:
        def prepare_send_message(self, session_id, req, user_id, org_id=None):
            captured["session_id"] = session_id
            captured["req"] = req
            captured["user_id"] = user_id
            captured["org_id"] = org_id
            return None

    async def fake_check_quota_status(user_id):
        return FakeQuotaStatus()

    monkeypatch.setattr(
        feishu_inbound_service,
        "get_stream_service",
        lambda: FakeStreamService(),
    )
    monkeypatch.setattr(
        feishu_inbound_service,
        "get_user_runtime_preference",
        lambda user_id: UserRuntimePreference(
            project_id=42,
            model="matmaster/test-model",
            org_id="org-1",
            user_bohrium_submit_confirmation_required=True,
        ),
    )
    monkeypatch.setattr(
        feishu_inbound_service,
        "check_quota_status",
        fake_check_quota_status,
    )
    monkeypatch.setattr(feishu_inbound_service, "REDIS_URL", "redis://test")
    monkeypatch.setattr(feishu_inbound_service, "reply_text_message", MagicMock())

    await feishu_inbound_service._run_agent_and_reply_feishu(
        user_id="user-1",
        session_id="sess-1",
        user_prompt="run",
        message_id="msg-1",
        tenant_token="tenant-token",
    )

    assert captured["session_id"] == "sess-1"
    assert captured["user_id"] == "user-1"
    assert captured["org_id"] == "org-1"
    assert captured["req"].bohrium_project_id == 42
    assert captured["req"].model == "matmaster/test-model"
    assert captured["req"].bohrium_submit_confirmation_required is True
