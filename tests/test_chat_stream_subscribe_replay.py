import asyncio
import json
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
        agent_run_service=MagicMock(),
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
        agent_run_service=MagicMock(),
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
