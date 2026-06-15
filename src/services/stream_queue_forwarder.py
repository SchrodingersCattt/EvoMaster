"""SSE 发起事件后的 subscribe-before-enqueue 转发引擎。"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable
from typing import Any

logger = logging.getLogger(__name__)

SubscriptionStarter = Callable[..., tuple[asyncio.Queue, Any, Any, Any]]
Subscription = tuple[asyncio.Queue, Any, Any, Any]


def _close_stream_subscription(subscription: Subscription) -> None:
    _redis_queue, shutdown_event, _subscribe_ready, sub_thread = subscription
    shutdown_event.set()
    sub_thread.join(timeout=2.0)


async def start_subscription_before_history_replay(
    session_id: str,
    *,
    start_stream_subscription: SubscriptionStarter,
    thread_name: str,
) -> Subscription:
    """先建立 live stream 订阅，再允许调用方 replay 历史事件。"""
    loop = asyncio.get_running_loop()
    subscription = start_stream_subscription(
        session_id,
        loop,
        thread_name=thread_name,
    )
    if not await asyncio.to_thread(subscription[2].wait, 3.0):
        logger.warning(
            'subscribe: redis subscribe not ready before replay session_id=%s',
            session_id,
        )
    return subscription


async def replay_history_and_follow_run_stream(
    service: Any,
    session_id: str,
    *,
    start_stream_subscription: SubscriptionStarter,
    initial_subscription: Subscription | None,
    is_run_on_another_pod: Callable[[], bool],
    is_run_queued: Callable[[], bool],
    redis_enabled: bool,
    thread_name: str,
) -> AsyncGenerator[str, None]:
    """replay 历史事件后继续转发远端 Worker 的 live stream。"""
    sid = session_id.strip()
    early_subscription = initial_subscription

    def _run_still_active() -> bool:
        return is_run_on_another_pod() or is_run_queued()

    stream_closed_seen = False

    async def _forward_stream_subscription(
        subscription: Subscription,
    ) -> AsyncGenerator[str, None]:
        nonlocal stream_closed_seen
        redis_queue, _shutdown_event, _subscribe_ready, _sub_thread = subscription
        try:
            while True:
                if not _run_still_active() and redis_queue.empty():
                    break
                try:
                    payload = await asyncio.wait_for(redis_queue.get(), timeout=30.0)
                except TimeoutError:
                    if not _run_still_active():
                        break
                    yield service.sse_format(service._ping_payload(sid))
                    continue
                if payload.get('type') == 'stream_closed':
                    stream_closed_seen = True
                    yield service.sse_format(payload)
                    break
                yield service.sse_format(payload)
        finally:
            _close_stream_subscription(subscription)

    try:
        for batch in service._iter_history_replay_batches(sid):
            yield batch

        if early_subscription is not None:
            subscription = early_subscription
            early_subscription = None
            async for chunk in _forward_stream_subscription(subscription):
                yield chunk
            if stream_closed_seen:
                return

        while _run_still_active():
            if is_run_on_another_pod():
                if redis_enabled:
                    loop = asyncio.get_running_loop()
                    subscription = start_stream_subscription(
                        sid,
                        loop,
                        thread_name=thread_name,
                    )
                    async for chunk in _forward_stream_subscription(subscription):
                        yield chunk
                    if stream_closed_seen:
                        break
                else:
                    await asyncio.sleep(5.0)
                    if not is_run_on_another_pod():
                        payload = service._sessions_service.get_session_status_payload(
                            sid
                        )
                        yield service.sse_format(payload)
                        break
                    yield service.sse_format(service._ping_payload(sid))
            else:
                await asyncio.sleep(5.0)
                yield service.sse_format(service._ping_payload(sid))
    finally:
        if early_subscription is not None:
            _close_stream_subscription(early_subscription)


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
