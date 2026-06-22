"""内部 HTTP trigger 的 subscribe-before-enqueue 时序：订阅就绪后才入队。"""

import asyncio
import threading
from unittest.mock import MagicMock, patch


def _make_service():
    from src.services.stream_service import ChatStreamService

    sessions = MagicMock()
    sessions.get_session_status_payload.return_value = {
        "source": "System",
        "type": "session_status",
        "status": "idle",
        "session_id": "s1",
    }
    return ChatStreamService(
        sessions_service=sessions,
        events_service=MagicMock(),
        deploy_state_service=MagicMock(),
    )


async def test_generate_internal_trigger_stream_subscribes_before_enqueue():
    from src.services.stream_service import TriggerStreamContext

    service = _make_service()
    ctx = TriggerStreamContext(
        task_id="trig_1",
        invocation_id="inv_1",
        owner="owner-1",
        job={"session_id": "s1"},
        event={"source": "System", "type": "trigger", "session_id": "s1"},
        dedup_key=None,
    )
    order: list[str] = []

    def _fake_sub(session_id, loop, *, thread_name):
        order.append("subscribe")
        ready = threading.Event()
        ready.set()
        q: asyncio.Queue = asyncio.Queue()
        q.put_nowait({"type": "stream_closed", "session_id": "s1"})
        return q, threading.Event(), ready, MagicMock()

    def _fake_enqueue(sid, job):
        order.append("enqueue")
        return True

    with (
        patch(
            "src.services.stream_service._start_redis_stream_subscription",
            side_effect=_fake_sub,
        ),
        patch("src.services.stream_service.get_redis_dao", return_value=MagicMock()),
        patch.object(service, "_iter_history_replay_batches", return_value=iter([])),
        patch.object(service, "_enqueue_run", side_effect=_fake_enqueue),
        patch.object(service, "_publish_user_wakeup") as pub,
    ):
        async for _ in service.generate_internal_trigger_stream("s1", ctx):
            pass

    assert order == ["subscribe", "enqueue"]
    pub.assert_called_once_with("owner-1", "s1", "trigger_enqueued")
