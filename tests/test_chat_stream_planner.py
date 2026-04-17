"""Planner 模式的 stream smoke + 归一化回归。

与 test_chat_stream_direct.py 姊妹关系：验证 mode='planner' 的请求能被
stream_service 正确归一化、入队、并保留在下游 context 中。更深入的
SSE 流行为（complete thought 过滤）由 test_sse_handler_mode_filter.py 覆盖。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


def test_prepare_send_message_accepts_planner_mode() -> None:
    """mode='planner' 合法，stream_service 不应将其 fallback 到 direct。"""
    from src.models.chat import ChatSendRequest
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.try_acquire_session_run.return_value = (True, None)
    events_service = MagicMock()
    deploy_state_service = MagicMock()
    fake_redis = MagicMock()

    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        agent_run_service=MagicMock(),
        deploy_state_service=deploy_state_service,
    )

    req = ChatSendRequest(content='brainstorm my plan', mode='planner')

    with (
        patch('src.services.stream_service.REDIS_URL', 'redis://test'),
        patch('src.services.stream_service.get_redis_dao', return_value=fake_redis),
    ):
        ctx = service.prepare_send_message(
            'sess-planner-1',
            req,
            user_id='user-1',
            org_id=None,
        )

    assert ctx is not None
    assert ctx.mode == 'planner'
    # 顺带确认用户消息事件里的 mode 也是 planner（历史重建需要）
    assert ctx.user_msg['mode'] == 'planner'


def test_prepare_send_message_falls_back_unknown_mode_to_default() -> None:
    """未知 mode 值归一化到 DEFAULT_MODE，不抛错。"""
    from matmaster.config.exp import DEFAULT_MODE
    from src.models.chat import ChatSendRequest
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.try_acquire_session_run.return_value = (True, None)
    events_service = MagicMock()
    deploy_state_service = MagicMock()
    fake_redis = MagicMock()

    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        agent_run_service=MagicMock(),
        deploy_state_service=deploy_state_service,
    )

    req = ChatSendRequest(content='hi', mode='bogus-mode-xyz')

    with (
        patch('src.services.stream_service.REDIS_URL', 'redis://test'),
        patch('src.services.stream_service.get_redis_dao', return_value=fake_redis),
    ):
        ctx = service.prepare_send_message(
            'sess-fallback-1',
            req,
            user_id='user-1',
            org_id=None,
        )

    assert ctx is not None
    assert ctx.mode == DEFAULT_MODE
    assert ctx.user_msg['mode'] == DEFAULT_MODE


@pytest.mark.asyncio
async def test_planner_job_enqueues_with_planner_mode_field() -> None:
    """入队的 Redis job payload 里 mode='planner' 被保留，Worker 能读到正确值。"""
    from src.services.stream_service import ChatStreamService, SendStreamContext

    service = ChatStreamService(
        sessions_service=MagicMock(
            get_session_status_payload=MagicMock(
                return_value={
                    'source': 'System',
                    'type': 'status',
                    'content': '',
                    'session_id': 'sess-planner',
                }
            )
        ),
        events_service=MagicMock(get_session_events=MagicMock(return_value=[])),
        agent_run_service=MagicMock(),
        deploy_state_service=MagicMock(),
    )

    ctx = SendStreamContext(
        task_id='task-planner-1',
        invocation_id='inv-planner-1',
        mode='planner',
        user_msg={
            'source': 'User',
            'type': 'query',
            'content': 'plan it',
            'mode': 'planner',
        },
        request_event_queue=asyncio.Queue(),
        reply_queue=MagicMock(),
    )

    fake_redis = MagicMock()
    fake_redis.create_client.return_value = None
    fake_redis.set_session_run_queued.return_value = True
    fake_redis.llen_agent_run_queue.return_value = 0
    fake_redis.lpush_agent_run_job.side_effect = lambda job: True

    async def _stream_closed_immediately(awaitable, timeout):
        close = getattr(awaitable, 'close', None)
        if callable(close):
            close()
        return {
            'source': 'System',
            'type': 'stream_closed',
            'content': '',
            'session_id': 'sess-planner',
        }

    with (
        patch('src.services.stream_service.REDIS_URL', 'redis://test'),
        patch('src.services.stream_service.get_redis_dao', return_value=fake_redis),
        patch(
            'src.services.stream_service.asyncio.wait_for',
            side_effect=_stream_closed_immediately,
        ),
    ):
        gen = service.generate_send_stream('sess-planner', 'plan it', ctx)
        await gen.__anext__()
        await gen.__anext__()
        await gen.__anext__()
        await gen.aclose()

    pushed_job = fake_redis.lpush_agent_run_job.call_args.args[0]
    assert pushed_job['mode'] == 'planner'
