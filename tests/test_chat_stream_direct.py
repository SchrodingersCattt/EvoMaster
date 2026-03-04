"""Direct stream 接口测试：跑真实 StreamingMatMaster 过程，用 Mock LLM + 内存 Mock 表，不依赖 DB/真实 API。"""

import json
import uuid
from unittest.mock import MagicMock, patch

from evomaster.utils.types import AssistantMessage, FunctionCall, ToolCall


def _make_mock_assistant_message():
    """返回真实 AssistantMessage：带 finish tool_call，让 agent 一步结束，避免跑满 200 轮（约 85s）。"""
    finish_args = '{"message":"Done","task_completed":"true"}'
    tool_calls = [
        ToolCall(
            id='mock-finish-1',
            type='function',
            function=FunctionCall(name='finish', arguments=finish_args),
        )
    ]
    return AssistantMessage(content='', tool_calls=tool_calls)


class MockLLM:
    """测试用 LLM：一次 query 即返回 finish tool_call，让 agent 单步结束。"""

    def query(self, dialog):
        return _make_mock_assistant_message()


def _mock_sessions_table():
    t = MagicMock()
    t.get_session.return_value = None
    t.create_session.return_value = None
    t.set_session_status.return_value = None
    t.set_session_last_task.return_value = None
    t.list_sessions.return_value = []
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


def _mock_bohrium_nodes_table():
    t = MagicMock()
    t.find_one_for_reuse.return_value = None
    t.insert_node.return_value = None
    t.update_last_used_at.return_value = None
    t.delete_by_node.return_value = None
    return t


async def _check_quota_noop(user_id: str) -> int:
    return 10


async def _use_quota_noop(user_id: str) -> None:
    pass


def test_chat_stream_direct_runs_real_agent_and_returns_sse():
    """POST /stream 带 content + mode=direct：跑真实 StreamingMatMaster，返回 SSE 且含 Initializing (direct)... 与 end。"""
    mock_sessions = _mock_sessions_table()
    mock_events = _mock_events_table()

    # 在 dao 层 patch get_*_table，使 sessions/events/agent_run 等所有调用方共用 mock；避免 patch 解析时 src.services 未加载子模块
    patches = [
        patch(
            'src.dao.chat_sessions_table.get_chat_sessions_table',
            return_value=mock_sessions,
        ),
        patch(
            'src.dao.chat_events_table.get_chat_events_table',
            return_value=mock_events,
        ),
        patch(
            'src.services.quota_service.check_quota',
            side_effect=_check_quota_noop,
        ),
        patch(
            'src.services.quota_service.use_quota',
            side_effect=_use_quota_noop,
        ),
        # 两处都 patch：子线程里 from evomaster.utils import create_llm 用 utils，避免 CI 仍走到真实 LLM 要 key
        patch(
            'evomaster.utils.create_llm',
            side_effect=lambda *a, **kw: MockLLM(),
        ),
        patch(
            'evomaster.utils.llm.create_llm',
            side_effect=lambda *a, **kw: MockLLM(),
        ),
        patch(
            'src.dao.bohrium_nodes_table.get_bohrium_nodes_table',
            return_value=_mock_bohrium_nodes_table(),
        ),
        # 无 Redis 时 record_session_version 会连库超时 ~75s，mock 掉
        patch(
            'src.services.deploy_state_service.DeployStateService.record_session_version',
            return_value=None,
        ),
    ]

    for p in patches:
        p.start()

    try:
        from src.services.agent_run_service import get_agent_run_service
        from src.services.events_service import get_events_service
        from src.services.sessions_service import get_sessions_service
        from src.services.stream_service import get_stream_service

        get_sessions_service.cache_clear()
        get_events_service.cache_clear()
        get_stream_service.cache_clear()
        get_agent_run_service.cache_clear()

        from fastapi.testclient import TestClient

        from app import app

        client = TestClient(app)
        session_id = f'test-direct-stream-{uuid.uuid4().hex[:12]}'
        url = f"/api/v1/chat/sessions/{session_id}/stream"
        headers = {'X-User-Id': 'test-user-3656033'}
        body = {'content': 'hello', 'mode': 'direct'}

        response = client.post(url, json=body, headers=headers)
        assert response.status_code == 200, response.text
        assert 'text/event-stream' in response.headers.get('content-type', '')

        text = response.text
        assert 'data:' in text

        payloads = []
        for block in text.split('\n\n'):
            block = block.strip()
            if not block:
                continue
            for line in block.split('\n'):
                line = line.strip()
                if line.startswith('data:'):
                    payload_str = line[5:].strip()
                    if not payload_str:
                        continue
                    try:
                        payloads.append(json.loads(payload_str))
                    except json.JSONDecodeError:
                        payloads.append({'raw': payload_str})
                    break

        types = [p.get('type') for p in payloads]
        contents = [str(p.get('content', '')) for p in payloads]

        assert 'session_status' in types or any(
            'direct' in c.lower() for c in contents
        ), f"Expected session_status or 'Initializing (direct)...' in SSE; got types={types}, contents={contents[:5]}"
        assert 'end' in types, f"Expected 'end' event in SSE; got types={types}"
    finally:
        for p in patches:
            p.stop()
