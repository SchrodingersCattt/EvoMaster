"""Stream 接口测试：仅 Worker 队列模式。无 REDIS_URL 时发送返回 503；有 Redis 时验证入队与 SSE 流（可选）。"""

import asyncio
import json
import uuid
from unittest.mock import MagicMock, patch

# 测试中屏蔽 DB：任何真实 BaseTable 触发的连接直接报错（应通过 get_*_table mock 避免走到这里）
_DB_DISABLED_ERROR = RuntimeError('DB disabled in test (use mock tables only)')


class _NoDbConnection:
    """占位 context manager：测试中禁止真实 DB 连接。"""

    def __enter__(self):
        raise _DB_DISABLED_ERROR

    def __exit__(self, *args):
        pass


def _mock_sessions_table():
    t = MagicMock()
    t.get_session.return_value = None
    t.create_session.return_value = None
    t.set_session_status.return_value = (
        True  # try_acquire_session_run 需其返回 True 才视为占用成功
    )
    t.set_session_last_task.return_value = None
    t.list_sessions.return_value = []
    t.count_sessions_by_user.return_value = 0
    t.count_active_sessions.return_value = 0
    t.reset_all_active_to_idle.return_value = 0
    t.set_share_status.return_value = False
    t.delete_session.return_value = False
    t.get_session.return_value = None
    return t


def _mock_events_table():
    t = MagicMock()
    t.get_session_events.return_value = []
    t.add_event.return_value = None
    return t


async def _check_quota_noop(user_id: str) -> int:
    return 10


def _decode_sse_payload(frame: str) -> dict:
    return json.loads(frame.split('data: ', 1)[1].strip())


def test_chat_stream_returns_503_when_redis_url_missing():
    """无 REDIS_URL 时 POST /stream 返回 503（仅 Worker 队列模式，发送需 Redis）。"""
    mock_sessions = _mock_sessions_table()
    mock_events = _mock_events_table()

    patches = [
        patch('src.apis.chat_api.REDIS_URL', None),
        patch(
            'src.base.base_table.BaseTable.get_connection',
            side_effect=lambda self: _NoDbConnection(),
        ),
        patch(
            'src.services.sessions_service.get_chat_sessions_table',
            return_value=mock_sessions,
        ),
        patch(
            'src.services.events_service.get_chat_events_table',
            return_value=mock_events,
        ),
        patch(
            'src.dao.chat_sessions_table.get_chat_sessions_table',
            return_value=mock_sessions,
        ),
        patch(
            'src.dao.chat_events_table.get_chat_events_table',
            return_value=mock_events,
        ),
        patch('src.apis.chat_api.check_quota', side_effect=_check_quota_noop),
    ]

    for p in patches:
        p.start()

    try:
        from src.services.events_service import get_events_service
        from src.services.sessions_service import get_sessions_service
        from src.services.stream_service import get_stream_service

        get_sessions_service.cache_clear()
        get_events_service.cache_clear()
        get_stream_service.cache_clear()

        from fastapi.testclient import TestClient

        from app import app

        client = TestClient(app)
        session_id = f'test-stream-503-{uuid.uuid4().hex[:12]}'
        url = f"/api/v1/chat/sessions/{session_id}/stream"
        headers = {'X-User-Id': 'test-user-3656033'}
        body = {'content': 'hello', 'mode': 'direct'}

        response = client.post(url, json=body, headers=headers)
        assert response.status_code == 503, response.text
        data = response.json()
        assert '队列' in data.get('msg', '') or 'REDIS' in data.get('msg', '')
    finally:
        for p in patches:
            p.stop()


def test_generate_send_stream_skips_current_task_in_history_replay():
    """发送流回放历史时不应再次回放当前任务刚落库的 query。"""
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
            'source': 'User',
            'type': 'query',
            'content': 'old question',
            'session_id': 'sess-1',
            'task_id': 'task-0',
        },
        {
            'source': 'MatMaster',
            'type': 'run_result',
            'content': 'old answer',
            'session_id': 'sess-1',
            'task_id': 'task-0',
        },
        {
            'source': 'User',
            'type': 'query',
            'content': 'new question',
            'session_id': 'sess-1',
            'task_id': 'task-1',
            'invocation_id': 'inv-1',
        },
    ]
    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        agent_run_service=MagicMock(),
        deploy_state_service=MagicMock(),
    )

    async def _collect_first_four_frames() -> list[dict]:
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
            reply_queue=MagicMock(),
        )
        gen = service.generate_send_stream('sess-1', 'new question', ctx)
        try:
            return [
                _decode_sse_payload(await gen.__anext__()),
                _decode_sse_payload(await gen.__anext__()),
                _decode_sse_payload(await gen.__anext__()),
                _decode_sse_payload(await gen.__anext__()),
            ]
        finally:
            await gen.aclose()

    frames = asyncio.run(_collect_first_four_frames())

    assert [frame['content'] for frame in frames[1:]] == [
        'old question',
        'old answer',
        'new question',
    ]
    assert frames[3]['type'] == 'query'
    assert frames[3]['mode'] == 'direct'


def test_sse_frames_match_frontend_contract_without_mysql():
    """无需 MySQL，直接验证最终 SSE frame 的 payload shape 可被前端消费。"""
    from matmaster.integration.event_router import SSEHandler
    from matmaster.types.events import (
        BohriumNodeEvent,
        ConfirmationRequestEvent,
        ErrorEvent,
        McpConnectEvent,
        McpServerStatusEvent,
        RunResultEvent,
        StreamClosedEvent,
        ToolCallEvent,
        ToolResultEvent,
    )
    from src.services.stream_service import ChatStreamService

    payloads = []
    handler = SSEHandler(
        send_cb=payloads.append,
        loop=None,
        session_id='sess-verify',
        task_id='task-verify',
        invocation_id='inv-verify',
        mode='direct',
    )

    events = [
        ToolCallEvent(
            source='Agent',
            call_id='call-1',
            tool_name='bash',
            arguments={'cmd': 'ls'},
        ),
        ToolResultEvent(
            source='Agent',
            call_id='call-1',
            tool_name='bash',
            result={'status': 'success', 'stdout': 'ok'},
            info={'auto_save': True},
        ),
        ConfirmationRequestEvent(
            source='MatMaster',
            question='Proceed?',
            mode='timeout',
            timeout_seconds=20,
            actions=['yes', 'no'],
            context='ctx',
            origin='planner',
        ),
        ErrorEvent(source='System', message='boom', traceback='tb'),
        BohriumNodeEvent(
            source='BohriumSetup',
            payload={
                'type': 'setup_ready',
                'content': {
                    'status': 'ready',
                    'message': 'Node ready',
                    'node_id': 1,
                },
                'phase': 'ssh',
            },
        ),
        McpServerStatusEvent(
            source='System',
            server_name='code-server',
            transport='sse',
            phase='retrying',
            detail={
                'message': 'retrying',
                'attempt': 2,
                'max_attempts': 3,
                'error': 'timeout',
            },
        ),
        McpConnectEvent(
            source='System',
            phase='ready',
            message='connected',
            elapsed_ms=123,
        ),
        RunResultEvent(source='Agent', reason='natural', final_content='done'),
        StreamClosedEvent(source='System', task_completed=True, end_reason='natural'),
    ]

    for event in events:
        handler.handle(event)

    frames = []
    for payload in payloads:
        frame = ChatStreamService.sse_format(payload)
        assert frame.startswith('event: ag-ui\n')
        frames.append(_decode_sse_payload(frame))

    assert [frame['type'] for frame in frames] == [
        'tool_call',
        'tool_result',
        'confirmation_request',
        'error',
        'bohrium_node',
        'mcp_server_status',
        'mcp_connect',
        'run_result',
        'stream_closed',
    ]
    assert all(isinstance(frame.get('timestamp'), str) for frame in frames)

    assert frames[0]['content'] == {
        'id': 'call-1',
        'call_id': 'call-1',
        'name': 'bash',
        'args': {'cmd': 'ls'},
    }
    assert frames[1]['content'] == {
        'id': 'call-1',
        'call_id': 'call-1',
        'name': 'bash',
        'result': {'status': 'success', 'stdout': 'ok'},
        'info': {'auto_save': True},
    }
    assert frames[2]['content'] == {
        'question': 'Proceed?',
        'mode': 'timeout',
        'timeout_seconds': 20,
        'context': 'ctx',
        'actions': ['yes', 'no'],
        'origin': 'planner',
    }
    assert frames[3]['content'] == {'message': 'boom', 'traceback': 'tb'}
    assert frames[4]['content'] == {
        'status': 'ready',
        'message': 'Node ready',
        'node_id': 1,
        'phase': 'ssh',
        'event_type': 'setup_ready',
    }
    assert frames[5]['content'] == {
        'server_name': 'code-server',
        'transport': 'sse',
        'phase': 'retrying',
        'message': 'retrying',
        'attempt': 2,
        'max_attempts': 3,
        'error': 'timeout',
    }
    assert frames[6]['content'] == {
        'phase': 'ready',
        'message': 'connected',
        'elapsed_ms': 123,
        'error': None,
    }
    assert frames[7]['final_content'] == 'done'
    assert frames[8]['task_completed'] is True
    assert frames[8]['end_reason'] == 'natural'
