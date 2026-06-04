import asyncio
from unittest.mock import MagicMock, patch

from src.services.stream_sse_filter import REPLAY_DISCARDED_EVENT_TYPES
from tests.test_chat_stream_direct import (
    _collect_n_frames,
    _decode_sse_payload,
    _send_stream_job,
)


def test_generate_send_stream_replay_keeps_response_figures_but_prefers_run_result():
    from src.services.stream_service import ChatStreamService, SendStreamContext

    sessions_service = MagicMock()
    sessions_service.get_session_status_payload.return_value = {
        'source': 'System',
        'type': 'status',
        'content': '',
        'session_id': 'sess-1',
    }
    events_service = MagicMock()
    events_service.get_session_events.return_value = [
        {
            'source': 'MatMaster',
            'type': 'response',
            'content': 'old answer',
            'session_id': 'sess-1',
            'task_id': 'task-0',
            'spawn_id': None,
        },
        {
            'source': 'System',
            'type': 'response_figures',
            'content': {
                'figures': [
                    {
                        'figure_id': 'band',
                        'asset_url': 'https://oss.example/band.png',
                        'caption': 'band',
                    }
                ]
            },
            'session_id': 'sess-1',
            'task_id': 'task-0',
            'spawn_id': None,
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
            'spawn_id': None,
        },
    ]
    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        agent_run_service=MagicMock(),
        deploy_state_service=MagicMock(),
    )

    async def _collect_frames() -> list[dict]:
        ctx = SendStreamContext(
            task_id='task-1',
            invocation_id='inv-1',
            mode='direct',
            user_msg={
                'source': 'User',
                'type': 'query',
                'content': 'new question',
                'mode': 'direct',
                'session_id': 'sess-1',
                'task_id': 'task-1',
                'invocation_id': 'inv-1',
            },
            request_event_queue=asyncio.Queue(),
            job=_send_stream_job(),
        )
        gen = service.generate_send_stream('sess-1', 'new question', ctx)
        try:
            return await _collect_n_frames(gen, 4)
        finally:
            await gen.aclose()

    with patch('src.services.stream_service.notify_post_async'):
        frames = asyncio.run(_collect_frames())

    assert [frame['type'] for frame in frames] == [
        'status',
        'response_figures',
        'run_result',
        'query',
    ]
    assert frames[1]['content']['figures'][0]['figure_id'] == 'band'
    assert frames[2]['final_content'] == 'old answer'
    assert frames[2]['status'] == 'completed'
    events_service.get_session_events.assert_called_with(
        'sess-1', include_spawn=True, exclude_types=REPLAY_DISCARDED_EVENT_TYPES
    )


def test_generate_subscribe_stream_replay_keeps_response_figures_but_prefers_run_result():
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
            'content': 'old answer',
            'session_id': 'sess-1',
            'task_id': 'task-0',
            'spawn_id': None,
        },
        {
            'source': 'System',
            'type': 'response_figures',
            'content': {
                'figures': [
                    {
                        'figure_id': 'band',
                        'asset_url': 'https://oss.example/band.png',
                        'caption': 'band',
                    }
                ]
            },
            'session_id': 'sess-1',
            'task_id': 'task-0',
            'spawn_id': None,
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
            'spawn_id': None,
        },
    ]

    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        agent_run_service=MagicMock(),
        deploy_state_service=MagicMock(),
    )

    async def _collect_frames() -> list[dict]:
        frames = []
        gen = service.generate_subscribe_stream('sess-1')
        try:
            async for chunk in gen:
                for part in chunk.split('\n\n'):
                    if part.strip():
                        frames.append(_decode_sse_payload(part))
        finally:
            await gen.aclose()
        return frames

    with patch('src.services.stream_service.REDIS_URL', None):
        frames = asyncio.run(_collect_frames())

    assert [frame['type'] for frame in frames] == [
        'status',
        'response_figures',
        'run_result',
    ]
    assert frames[1]['content']['figures'][0]['figure_id'] == 'band'
    assert frames[2]['final_content'] == 'old answer'
    assert frames[2]['status'] == 'completed'
    events_service.get_session_events.assert_called_with(
        'sess-1', include_spawn=True, exclude_types=REPLAY_DISCARDED_EVENT_TYPES
    )
