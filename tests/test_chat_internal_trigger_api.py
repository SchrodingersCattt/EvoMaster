"""/stream 内部发起（X-Internal-Token）鉴权与分派测试。"""

import uuid
from unittest.mock import MagicMock, patch


async def _check_quota_ok(user_id: str):
    from src.services.quota_service import QuotaStatus

    return QuotaStatus(remaining_yuan=10.0, reset_at=None)


def _client_with_overrides(fake_sessions, fake_stream):
    from fastapi.testclient import TestClient

    from app import app
    from src.services.sessions_service import get_sessions_service
    from src.services.stream_service import get_stream_service

    app.dependency_overrides[get_sessions_service] = lambda: fake_sessions
    app.dependency_overrides[get_stream_service] = lambda: fake_stream
    return TestClient(app), app, get_sessions_service, get_stream_service


def _clear_overrides(app, *dependencies):
    for dep in dependencies:
        app.dependency_overrides.pop(dep, None)


def test_internal_trigger_enqueues_with_valid_token():
    from src.services.stream_service import TriggerStreamContext

    fake_stream = MagicMock()
    fake_stream.prepare_internal_trigger_run.return_value = TriggerStreamContext(
        task_id="trig_abc",
        invocation_id="inv_abc",
        owner="owner-1",
        job={"session_id": "sess"},
        event={"source": "System", "type": "trigger"},
        dedup_key="job:123:done",
    )

    async def _empty_stream(_sid, _ctx):
        if False:
            yield ""

    fake_stream.generate_internal_trigger_stream.side_effect = (
        lambda sid, ctx: _empty_stream(sid, ctx)
    )

    fake_sessions = MagicMock()
    fake_sessions.get_session_user_id.return_value = "owner-1"

    patches = [
        patch("src.apis.chat_api.REDIS_URL", "redis://test"),
        patch("src.apis.chat_api.INTERNAL_TRIGGER_TOKEN", "secret-token"),
        patch("src.apis.chat_api.check_quota_status", side_effect=_check_quota_ok),
    ]
    for p in patches:
        p.start()
    client, app, sessions_dep, stream_dep = _client_with_overrides(
        fake_sessions, fake_stream
    )
    try:
        sid = f"sess-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            f"/api/v1/chat/sessions/{sid}/stream",
            json={
                "content": "作业123已完成",
                "origin": "hpc_job",
                "dedup_key": "job:123:done",
                "delivery": {"notify": True},
            },
            headers={"X-Internal-Token": "secret-token"},
        )
        assert resp.status_code == 200, resp.text
        fake_stream.prepare_internal_trigger_run.assert_called_once()
        kwargs = fake_stream.prepare_internal_trigger_run.call_args.kwargs
        assert kwargs["origin"] == "hpc_job"
        assert kwargs["dedup_key"] == "job:123:done"
        fake_sessions.can_access_session.assert_not_called()
    finally:
        _clear_overrides(app, sessions_dep, stream_dep)
        for p in patches:
            p.stop()


def test_internal_trigger_deduped_returns_json_not_stream():
    from src.services.stream_service import TriggerResult

    fake_stream = MagicMock()
    fake_stream.prepare_internal_trigger_run.return_value = TriggerResult(
        status="deduped", dedup_key="job:123:done"
    )
    fake_sessions = MagicMock()
    fake_sessions.get_session_user_id.return_value = "owner-1"

    patches = [
        patch("src.apis.chat_api.REDIS_URL", "redis://test"),
        patch("src.apis.chat_api.INTERNAL_TRIGGER_TOKEN", "secret-token"),
        patch("src.apis.chat_api.check_quota_status", side_effect=_check_quota_ok),
    ]
    for p in patches:
        p.start()
    client, app, sessions_dep, stream_dep = _client_with_overrides(
        fake_sessions, fake_stream
    )
    try:
        sid = f"sess-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            f"/api/v1/chat/sessions/{sid}/stream",
            json={"content": "x", "origin": "hpc_job", "dedup_key": "job:123:done"},
            headers={"X-Internal-Token": "secret-token"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["data"]["status"] == "deduped"
        fake_stream.generate_internal_trigger_stream.assert_not_called()
    finally:
        _clear_overrides(app, sessions_dep, stream_dep)
        for p in patches:
            p.stop()


def test_internal_trigger_wrong_token_rejected_fail_closed():
    """只要带了 X-Internal-Token 但不匹配，就直接拒绝，不回落普通用户鉴权。"""
    fake_stream = MagicMock()
    fake_sessions = MagicMock()

    patches = [
        patch("src.apis.chat_api.REDIS_URL", "redis://test"),
        patch("src.apis.chat_api.INTERNAL_TRIGGER_TOKEN", "secret-token"),
    ]
    for p in patches:
        p.start()
    client, app, sessions_dep, stream_dep = _client_with_overrides(
        fake_sessions, fake_stream
    )
    try:
        sid = f"sess-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            f"/api/v1/chat/sessions/{sid}/stream",
            json={"content": "x", "origin": "hpc_job"},
            headers={"X-Internal-Token": "wrong-token"},
        )
        assert resp.status_code == 403, resp.text
        fake_stream.prepare_internal_trigger_run.assert_not_called()
        fake_sessions.can_access_session.assert_not_called()
    finally:
        _clear_overrides(app, sessions_dep, stream_dep)
        for p in patches:
            p.stop()


def test_internal_trigger_rejected_on_share_route():
    """分享页路由保持只读，即使带合法内部 token 也不能触发 run。"""
    fake_stream = MagicMock()
    fake_sessions = MagicMock()

    patches = [
        patch("src.apis.chat_api.REDIS_URL", "redis://test"),
        patch("src.apis.chat_api.INTERNAL_TRIGGER_TOKEN", "secret-token"),
    ]
    for p in patches:
        p.start()
    client, app, sessions_dep, stream_dep = _client_with_overrides(
        fake_sessions, fake_stream
    )
    try:
        sid = f"sess-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            f"/pubapi/v1/chat/sessions/{sid}/stream",
            json={"content": "x", "origin": "hpc_job"},
            headers={"X-Internal-Token": "secret-token"},
        )
        assert resp.status_code == 403, resp.text
        fake_stream.prepare_internal_trigger_run.assert_not_called()
        fake_sessions.can_access_session.assert_not_called()
    finally:
        _clear_overrides(app, sessions_dep, stream_dep)
        for p in patches:
            p.stop()
