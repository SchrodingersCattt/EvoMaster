"""SSE 发起事件后的 subscribe-before-enqueue 转发引擎。"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable
from typing import Any

logger = logging.getLogger(__name__)

SubscriptionStarter = Callable[..., tuple[asyncio.Queue, Any, Any, Any]]


async def subscribe_enqueue_and_forward(
    service: Any,
    session_id: str,
    *,
    initiating_event: dict,
    task_id: str,
    invocation_id: str,
    thread_name: str,
    enqueue: Callable[[], bool],
    start_stream_subscription: SubscriptionStarter,
) -> AsyncGenerator[str, None]:
    """先订阅，订阅就绪后入队，再转发 Worker 实时事件。"""
    sid = session_id.strip()
    loop = asyncio.get_running_loop()
    start_time_ms = int(time.time() * 1000)

    payload = service._sessions_service.get_session_status_payload(sid)
    payload['stream_started_at'] = start_time_ms
    payload['invocation_id'] = invocation_id
    yield service.sse_format(payload)
    for batch in service._iter_history_replay_batches(sid, exclude_task_id=task_id):
        yield batch
    yield service.sse_format(initiating_event)

    (
        redis_queue,
        shutdown_event,
        subscribe_ready,
        sub_thread,
    ) = start_stream_subscription(
        sid,
        loop,
        thread_name=thread_name,
    )

    try:
        if not await asyncio.to_thread(subscribe_ready.wait, 3.0):
            logger.warning(
                'subscribe_enqueue_and_forward: redis subscribe not ready before '
                'enqueue session_id=%s task_id=%s',
                sid,
                task_id,
            )
        if not enqueue():
            yield service.sse_format(
                {
                    'source': 'System',
                    'type': 'error',
                    'content': 'Queue unavailable.',
                    'session_id': sid,
                    'invocation_id': invocation_id,
                }
            )
            yield service.sse_format(
                {
                    'source': 'System',
                    'type': 'stream_closed',
                    'content': '',
                    'session_id': sid,
                    'invocation_id': invocation_id,
                }
            )
            return
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
                'invocation_id': payload.get('invocation_id') or invocation_id,
            }
            yield service.sse_format(out)
            if payload.get('type') == 'stream_closed':
                break
    finally:
        shutdown_event.set()
        sub_thread.join(timeout=2.0)
