import asyncio
import json
import threading
from unittest.mock import MagicMock, patch

from src.services.stream_sse_filter import REPLAY_DISCARDED_EVENT_TYPES


def _decode_sse_payload(frame: str) -> dict:
    return json.loads(frame.split('data: ', 1)[1].strip())


def test_generate_subscribe_stream_normalizes_replayed_history_source():
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.get_session_status_payload.return_value = {
        'source': 'System',
        'type': 'status',
        'content': '',
        'session_id': 'sess-1',
        'status': 'idle',
    }
    sessions_service.is_session_running_on_this_pod.return_value = False
    sessions_service.is_session_run_on_another_pod.return_value = False

    events_service = MagicMock()
    events_service.get_session_events.return_value = [
        {
            'source': 'Planner',
            'type': 'run_result',
            'content': 'old answer',
            'session_id': 'sess-1',
            'task_id': 'task-0',
        }
    ]

    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=MagicMock(),
    )

    async def _collect_frames() -> list[dict]:
        gen = service.generate_subscribe_stream('sess-1')
        try:
            return [
                _decode_sse_payload(await gen.__anext__()),
                _decode_sse_payload(await gen.__anext__()),
            ]
        finally:
            await gen.aclose()

    with patch('src.services.stream_service.REDIS_URL', None):
        frames = asyncio.run(_collect_frames())

    history_frames = [frame for frame in frames if frame['type'] == 'run_result']
    assert len(history_frames) == 1
    assert history_frames[0]['source'] == 'MatMaster'
    assert history_frames[0]['content'] == 'old answer'
    events_service.get_session_events.assert_called_with(
        'sess-1', include_spawn=True, exclude_types=REPLAY_DISCARDED_EVENT_TYPES
    )


def test_generate_subscribe_stream_replay_prefers_run_result_over_response():
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.get_session_status_payload.return_value = {
        'source': 'System',
        'type': 'status',
        'content': '',
        'session_id': 'sess-1',
        'status': 'idle',
    }
    sessions_service.is_session_running_on_this_pod.return_value = False
    sessions_service.is_session_run_on_another_pod.return_value = False

    events_service = MagicMock()
    events_service.get_session_events.return_value = [
        {
            'source': 'MatMaster',
            'type': 'response',
            'content': {
                'content': 'old answer',
                'model': 'provider/private-model',
            },
            'session_id': 'sess-1',
            'task_id': 'task-0',
        },
        {
            'source': 'MatMaster',
            'type': 'run_result',
            'content': {
                'content': 'old answer',
                'status': 'completed',
                'reason': 'natural',
            },
            'session_id': 'sess-1',
            'task_id': 'task-0',
        },
    ]

    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=MagicMock(),
    )

    async def _collect_frames() -> list[dict]:
        frames = []
        gen = service.generate_subscribe_stream('sess-1')
        try:
            async for frame in gen:
                frames.append(_decode_sse_payload(frame))
        finally:
            await gen.aclose()
        return frames

    with patch('src.services.stream_service.REDIS_URL', None):
        frames = asyncio.run(_collect_frames())

    assert [frame['type'] for frame in frames] == ['status', 'run_result']
    assert frames[1]['final_content'] == 'old answer'
    assert frames[1]['status'] == 'completed'
    events_service.get_session_events.assert_called_with(
        'sess-1', include_spawn=True, exclude_types=REPLAY_DISCARDED_EVENT_TYPES
    )


def test_generate_subscribe_stream_replay_batches_frames_without_loss():
    """大量历史事件回放时合并成更少的 yield，但帧顺序与内容应完全保留。"""
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.get_session_status_payload.return_value = {
        'source': 'System',
        'type': 'status',
        'content': '',
        'session_id': 'sess-1',
        'status': 'idle',
    }
    sessions_service.is_session_running_on_this_pod.return_value = False
    sessions_service.is_session_run_on_another_pod.return_value = False

    # 每条带 ~2KB content，>32 条即可越过 64KB 批阈值，确保产生多个合并块。
    big = 'x' * 2048
    history = [
        {
            'source': 'Planner',
            'type': 'run_result',
            'content': f'{i}-{big}',
            'session_id': 'sess-1',
            'task_id': f'task-{i}',
        }
        for i in range(80)
    ]
    events_service = MagicMock()
    events_service.get_session_events.return_value = history

    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=MagicMock(),
    )

    async def _collect_chunks() -> list[str]:
        chunks = []
        gen = service.generate_subscribe_stream('sess-1')
        try:
            async for chunk in gen:
                chunks.append(chunk)
        finally:
            await gen.aclose()
        return chunks

    with patch('src.services.stream_service.REDIS_URL', None):
        chunks = asyncio.run(_collect_chunks())

    frames = [
        _decode_sse_payload(part)
        for chunk in chunks
        for part in chunk.split('\n\n')
        if part.strip()
    ]
    run_results = [f for f in frames if f['type'] == 'run_result']
    # 80 条历史事件无损还原，且顺序保持。
    assert len(run_results) == 80
    assert [f['content'] for f in run_results] == [f'{i}-{big}' for i in range(80)]
    # 关键：合并后 yield 次数远少于帧数（否则就没起到合并作用）。
    assert len(chunks) < len(frames)
    # 至少有一个 yield 块里塞了多条帧。
    assert any(chunk.count('event: ') > 1 for chunk in chunks)


def test_generate_subscribe_stream_replays_subagent_spawn_binding():
    from src.services.stream_service import ChatStreamService

    assert 'subagent_spawn' not in REPLAY_DISCARDED_EVENT_TYPES

    sessions_service = MagicMock()
    sessions_service.get_session_status_payload.return_value = {
        'source': 'System',
        'type': 'status',
        'content': '',
        'session_id': 'sess-1',
        'status': 'idle',
    }
    sessions_service.is_session_running_on_this_pod.return_value = False
    sessions_service.is_session_run_on_another_pod.return_value = False

    events_service = MagicMock()
    events_service.get_session_events.return_value = [
        {
            'source': 'MatMaster:direct',
            'type': 'subagent_spawn',
            'content': {
                'parent_call_id': 'call_x',
                'exp_name': 'direct',
                'task_summary': 'summarize logs',
            },
            'session_id': 'sess-1',
            'task_id': 'task-0',
            'spawn_id': 'ab12cd34ef56ab12',
        }
    ]

    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=MagicMock(),
    )

    async def _collect_frames() -> list[dict]:
        frames = []
        gen = service.generate_subscribe_stream('sess-1')
        try:
            async for frame in gen:
                frames.append(_decode_sse_payload(frame))
        finally:
            await gen.aclose()
        return frames

    with patch('src.services.stream_service.REDIS_URL', None):
        frames = asyncio.run(_collect_frames())

    binding_frames = [f for f in frames if f['type'] == 'subagent_spawn']
    assert len(binding_frames) == 1
    assert binding_frames[0]['spawn_id'] == 'ab12cd34ef56ab12'
    assert binding_frames[0]['source'] == 'MatMaster:direct'
    assert binding_frames[0]['content']['parent_call_id'] == 'call_x'
    events_service.get_session_events.assert_called_with(
        'sess-1', include_spawn=True, exclude_types=REPLAY_DISCARDED_EVENT_TYPES
    )


async def test_generate_subscribe_stream_subscribes_before_replay_for_running_session():
    """后台 trigger 已入队后补接会话流时，先订阅 live channel 再查历史。"""
    from src.services.stream_service import ChatStreamService

    order: list[str] = []
    sessions_service = MagicMock()
    sessions_service.get_session_status_payload.return_value = {
        'source': 'System',
        'type': 'status',
        'content': '',
        'session_id': 'sess-1',
        'status': 'waiting',
    }
    sessions_service.is_session_running_on_this_pod.return_value = False
    run_on_another_pod_states = iter(
        [
            False,  # initial logging/stale check before worker takes the queued run
            True,  # _run_still_active after replay
            True,  # branch into Redis subscription forwarding
            True,  # inner forwarding loop consumes stream_closed
            False,  # outer loop exits after stream_closed
        ]
    )
    sessions_service.is_session_run_on_another_pod.side_effect = lambda _sid: next(
        run_on_another_pod_states, False
    )

    redis_dao = MagicMock()
    queued_states = iter([True, False])
    redis_dao.is_session_run_queued.side_effect = lambda _sid: next(
        queued_states, False
    )

    events_service = MagicMock()
    events_service.get_session_events.side_effect = (
        lambda *args, **kwargs: order.append('history') or []
    )

    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=MagicMock(),
    )

    def _fake_sub(session_id, loop, *, thread_name):
        order.append('subscribe')
        ready = threading.Event()
        ready.set()
        queue: asyncio.Queue = asyncio.Queue()
        queue.put_nowait({'type': 'stream_closed', 'session_id': session_id})
        return queue, threading.Event(), ready, MagicMock()

    with (
        patch('src.services.stream_service.REDIS_URL', 'redis://test'),
        patch('src.services.stream_service.get_redis_dao', return_value=redis_dao),
        patch(
            'src.services.stream_service._start_redis_stream_subscription',
            side_effect=_fake_sub,
        ),
    ):
        async for _ in service.generate_subscribe_stream('sess-1'):
            pass

    assert order == ['subscribe', 'history']
