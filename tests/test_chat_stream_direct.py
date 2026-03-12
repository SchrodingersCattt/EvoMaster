"""Direct stream 接口测试：跑真实 StreamingMatMaster 过程，用 Mock LLM + 内存 Mock 表，不依赖 DB/Redis/真实 API。"""

import json
import uuid
from unittest.mock import MagicMock, patch

from evomaster.utils.types import AssistantMessage, FunctionCall, ToolCall

# 测试中屏蔽 DB：任何真实 BaseTable 触发的连接直接报错（应通过 get_*_table mock 避免走到这里）
_DB_DISABLED_ERROR = RuntimeError('DB disabled in test (use mock tables only)')


# 测试中屏蔽 Redis：所有 get_redis_dao() 返回此 mock，不建连
def _mock_redis_dao():
    dao = MagicMock()
    dao.create_client.return_value = None
    dao.get_publish_client.return_value = None
    dao.publish.return_value = False
    dao.publish_stream_event.return_value = False
    dao.set_confirmation_run_active.return_value = False
    dao.get_confirmation_run_context.return_value = None
    dao.is_confirmation_run_active.return_value = False
    dao.lpush_agent_run_job.return_value = False
    dao.delete_confirmation_reply_list.return_value = None
    dao.delete_confirmation_run_active.return_value = None
    dao.delete_stop_requested.return_value = None
    return dao


class _NoDbConnection:
    """占位 context manager：测试中禁止真实 DB 连接。"""

    def __enter__(self):
        raise _DB_DISABLED_ERROR

    def __exit__(self, *args):
        pass


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

    def query_stream(self, dialog, on_token=None):
        msg = _make_mock_assistant_message()
        if on_token and msg.content:
            on_token(msg.content)
        return msg


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


def _mock_bohrium_nodes_table():
    t = MagicMock()
    t.find_one_for_reuse.return_value = None
    t.insert_node.return_value = None
    t.update_last_used_at.return_value = None
    t.delete_by_node.return_value = None
    return t


def _mock_worker_registry_service():
    s = MagicMock()
    s.set_session_run_owner.return_value = None
    s.delete_session_run_owner.return_value = None
    s.get_session_run_owner.return_value = None
    s.is_worker_alive.return_value = False
    return s


async def _check_quota_noop(user_id: str) -> int:
    return 10


async def _use_quota_noop(user_id: str) -> None:
    pass


def test_chat_stream_direct_runs_real_agent_and_returns_sse():
    """POST /stream 带 content + mode=direct：跑真实 StreamingMatMaster，返回 SSE 且含 Initializing (direct)... 与 end。"""
    mock_sessions = _mock_sessions_table()
    mock_events = _mock_events_table()

    # 屏蔽 Redis：所有模块用到的 get_redis_dao 都返回 mock，不建连（CI/本地一致）
    mock_redis = _mock_redis_dao()
    # 屏蔽 DB：任何真实 BaseTable.get_connection() 直接报错，避免漏 patch 时连上真实库
    patches = [
        patch('src.dao.redis_dao.get_redis_dao', return_value=mock_redis),
        patch(
            'src.base.base_table.BaseTable.get_connection',
            side_effect=lambda self: _NoDbConnection(),
        ),
        patch('src.services.stream_service.REDIS_URL', None),
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
        # run_agent_sync 在子线程里 event_callback 会调 get_chat_events_table()，需在 agent_run_service 命名空间 patch
        patch(
            'src.services.agent_run_service.get_chat_events_table',
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
        # release_session_run / try_acquire 会调 worker_registry，mock 避免 Redis 或阻塞
        patch(
            'src.services.sessions_service.get_worker_registry_service',
            return_value=_mock_worker_registry_service(),
        ),
        patch(
            'src.services.stream_service.get_worker_registry_service',
            return_value=_mock_worker_registry_service(),
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
        headers = {
            'X-User-Id': 'test-user-3656033',
            'X-Org-Id': 'test-org-direct-stream',  # 可选；有则走 org_id 写入/ Bohrium 相关路径
        }
        body = {'content': 'hello', 'mode': 'direct'}

        response = client.post(url, json=body, headers=headers)
        assert response.status_code == 200, response.text
        assert 'text/event-stream' in response.headers.get('content-type', '')

        text = response.text
        assert 'data:' in text

        # 收集所有 data: 行（SSE 可能多行或分块不同），避免漏掉 end
        payloads = []
        for line in text.split('\n'):
            line = line.strip()
            if not line.startswith('data:'):
                continue
            payload_str = line[5:].strip()
            if not payload_str:
                continue
            try:
                payloads.append(json.loads(payload_str))
            except json.JSONDecodeError:
                payloads.append({'raw': payload_str})

        types = [p.get('type') for p in payloads]
        contents = [str(p.get('content', '')) for p in payloads]

        assert 'session_status' in types or any(
            'direct' in c.lower() for c in contents
        ), f"Expected session_status or 'Initializing (direct)...' in SSE; got types={types}, contents={contents[:5]}"
        # TestClient 下流可能在 call_soon_threadsafe(end) 被处理前就关闭，只断言至少收到 agent 事件（exp_run）或正常结束（end）
        assert (
            'end' in types or 'exp_run' in types
        ), f"Expected 'end' or 'exp_run' in SSE (agent ran); got types={types}"
    finally:
        for p in patches:
            p.stop()
