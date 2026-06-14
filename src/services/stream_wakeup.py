"""用户级 wakeup 与内部 trigger SSE generator 实现。"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable
from typing import Any

from src.dao.redis_dao import get_redis_dao, user_wakeup_channel

logger = logging.getLogger(__name__)

SubscriptionStarter = Callable[..., tuple[asyncio.Queue, Any, Any, Any]]


async def generate_wakeup_stream_impl(
    service: Any,
    user_id: str,
    *,
    redis_url: str | None,
    start_channel_subscription: SubscriptionStarter,
) -> AsyncGenerator[str, None]:
    """用户级 wakeup 流：订阅就绪后发送 snapshot，再转发 live wakeup。"""
    uid = (user_id or "").strip()

    def _snapshot_frames() -> list[str]:
        return [
            service.sse_format(
                {
                    "source": "System",
                    "type": "session_wakeup",
                    "reason": "session_waiting_snapshot",
                    "session_id": sid,
                }
            )
            for sid in service._sessions_service.list_waiting_or_active_session_ids(uid)
        ]

    if not redis_url:
        for frame in _snapshot_frames():
            yield frame
        return

    loop = asyncio.get_running_loop()
    channel = user_wakeup_channel(uid)
    (
        redis_queue,
        shutdown_event,
        subscribe_ready,
        sub_thread,
    ) = start_channel_subscription(
        channel,
        loop,
        thread_name=f"wakeup-{uid[:8]}",
    )
    try:
        if not await asyncio.to_thread(subscribe_ready.wait, 3.0):
            logger.warning(
                "generate_wakeup_stream: redis subscribe not ready before "
                "snapshot user_id=%s",
                uid,
            )
        for frame in _snapshot_frames():
            yield frame
        while True:
            try:
                payload = await asyncio.wait_for(redis_queue.get(), timeout=30.0)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield service.sse_format(payload)
    finally:
        shutdown_event.set()
        sub_thread.join(timeout=2.0)


async def generate_internal_trigger_stream_impl(
    service: Any,
    session_id: str,
    ctx: Any,
    *,
    start_stream_subscription: SubscriptionStarter,
) -> AsyncGenerator[str, None]:
    """内部 HTTP trigger 流：订阅就绪后才入队，再转发 Worker 实时事件。"""
    sid = session_id.strip()
    loop = asyncio.get_running_loop()
    start_time_ms = int(time.time() * 1000)
    logger.info(
        'generate_internal_trigger_stream: start session_id=%s task_id=%s',
        sid,
        ctx.task_id,
    )

    payload = service._sessions_service.get_session_status_payload(sid)
    payload['stream_started_at'] = start_time_ms
    payload['invocation_id'] = ctx.invocation_id
    yield service.sse_format(payload)
    for batch in service._iter_history_replay_batches(sid, exclude_task_id=ctx.task_id):
        yield batch
    yield service.sse_format(ctx.event)

    (
        redis_queue,
        shutdown_event,
        subscribe_ready,
        sub_thread,
    ) = start_stream_subscription(
        sid,
        loop,
        thread_name=f"trigger-stream-{sid[:8]}",
    )

    try:
        if not await asyncio.to_thread(subscribe_ready.wait, 3.0):
            logger.warning(
                'generate_internal_trigger_stream: redis subscribe not ready '
                'before enqueue session_id=%s task_id=%s',
                sid,
                ctx.task_id,
            )
        if not service._enqueue_run(sid, ctx.job):
            yield service.sse_format(
                {
                    'source': 'System',
                    'type': 'error',
                    'content': 'Queue unavailable.',
                    'session_id': sid,
                    'invocation_id': ctx.invocation_id,
                }
            )
            yield service.sse_format(
                {
                    'source': 'System',
                    'type': 'stream_closed',
                    'content': '',
                    'session_id': sid,
                    'invocation_id': ctx.invocation_id,
                }
            )
            return
        if ctx.dedup_key:
            get_redis_dao().mark_dedup_key_nx(ctx.dedup_key, ctx.task_id)
        service._publish_user_wakeup(ctx.owner, sid, "trigger_enqueued")
        while True:
            try:
                payload = await asyncio.wait_for(redis_queue.get(), timeout=30.0)
            except TimeoutError:
                yield service.sse_format(service._ping_payload(sid))
                continue
            elapsed_ms = int(time.time() * 1000) - start_time_ms
            out = {
                **payload,
                'elapsed_ms': elapsed_ms,
                'stream_started_at': start_time_ms,
                'invocation_id': payload.get('invocation_id') or ctx.invocation_id,
            }
            yield service.sse_format(out)
            if payload.get('type') == 'stream_closed':
                break
    finally:
        shutdown_event.set()
        sub_thread.join(timeout=2.0)
