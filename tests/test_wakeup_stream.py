"""用户级 wakeup stream：subscribe-before-snapshot + live 转发 + 端点鉴权。"""

import asyncio
import json
import threading
from unittest.mock import MagicMock, patch


def _make_service(sessions=None):
    from src.services.stream_service import ChatStreamService

    return ChatStreamService(
        sessions_service=sessions or MagicMock(),
        events_service=MagicMock(),
        deploy_state_service=MagicMock(),
    )


def _frames_from_chunk(chunk: str) -> list[dict]:
    return [
        json.loads(part.split("data: ", 1)[1])
        for part in chunk.split("\n\n")
        if part.strip() and part.lstrip().startswith("event:")
    ]


async def test_snapshot_emits_one_wakeup_per_waiting_active_session():
    sessions = MagicMock()
    sessions.list_waiting_or_active_session_ids.return_value = ["s1", "s2"]
    service = _make_service(sessions)

    frames: list[dict] = []
    with patch("src.services.stream_service.REDIS_URL", None):
        async for chunk in service.generate_wakeup_stream("user-1"):
            frames.extend(_frames_from_chunk(chunk))

    sessions.list_waiting_or_active_session_ids.assert_called_once_with("user-1")
    assert [f["session_id"] for f in frames] == ["s1", "s2"]
    for f in frames:
        assert f["type"] == "session_wakeup"
        assert f["reason"] == "session_waiting_snapshot"
        assert set(f.keys()) == {"source", "type", "reason", "session_id"}


async def test_subscribes_before_snapshot_query():
    sessions = MagicMock()
    order: list[str] = []
    sessions.list_waiting_or_active_session_ids.side_effect = lambda uid: (
        order.append("snapshot") or ["s1"]
    )
    service = _make_service(sessions)

    def _fake_sub(channel, loop, *, thread_name):
        order.append("subscribe")
        ready = threading.Event()
        ready.set()
        return asyncio.Queue(), threading.Event(), ready, MagicMock()

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch(
            "src.services.stream_service._start_redis_channel_subscription",
            side_effect=_fake_sub,
        ),
    ):
        gen = service.generate_wakeup_stream("user-1")
        first = await gen.__anext__()
        await gen.aclose()

    assert order == ["subscribe", "snapshot"]
    payload = _frames_from_chunk(first)[0]
    assert payload["session_id"] == "s1"
    assert payload["reason"] == "session_waiting_snapshot"


async def test_forwards_live_wakeup_then_closes_on_client_disconnect():
    sessions = MagicMock()
    sessions.list_waiting_or_active_session_ids.return_value = []
    service = _make_service(sessions)
    live = {
        "source": "System",
        "type": "session_wakeup",
        "reason": "trigger_enqueued",
        "session_id": "s9",
    }

    def _fake_sub(channel, loop, *, thread_name):
        assert channel == "chat:user:user-1:wakeup"
        ready = threading.Event()
        ready.set()
        q: asyncio.Queue = asyncio.Queue()
        q.put_nowait(live)
        return q, threading.Event(), ready, MagicMock()

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch(
            "src.services.stream_service._start_redis_channel_subscription",
            side_effect=_fake_sub,
        ),
    ):
        gen = service.generate_wakeup_stream("user-1")
        chunk = await gen.__anext__()
        await gen.aclose()

    payload = _frames_from_chunk(chunk)[0]
    assert payload == live


def test_wakeup_endpoint_requires_login():
    from fastapi.testclient import TestClient

    from app import app

    client = TestClient(app)
    resp = client.get("/api/v1/chat/wakeup/stream")
    assert resp.status_code == 401, resp.text


def test_wakeup_endpoint_success_invokes_generator():
    from fastapi.testclient import TestClient

    from app import app
    from src.services.stream_service import get_stream_service

    fake_stream = MagicMock()

    async def _empty(_uid):
        if False:
            yield ""

    fake_stream.generate_wakeup_stream.side_effect = lambda uid: _empty(uid)
    app.dependency_overrides[get_stream_service] = lambda: fake_stream
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/chat/wakeup/stream", headers={"X-User-Id": "user-1"}
        )
        assert resp.status_code == 200, resp.text
        fake_stream.generate_wakeup_stream.assert_called_once_with("user-1")
    finally:
        app.dependency_overrides.pop(get_stream_service, None)


def test_wakeup_not_exposed_on_share_route():
    from fastapi.testclient import TestClient

    from app import app

    client = TestClient(app)
    resp = client.get(
        "/pubapi/v1/chat/wakeup/stream", headers={"X-User-Id": "user-1"}
    )
    assert resp.status_code == 404, resp.text
