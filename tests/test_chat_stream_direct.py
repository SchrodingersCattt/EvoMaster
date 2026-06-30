"""Stream 接口测试：仅 Worker 队列模式。无 REDIS_URL 时发送返回 503；有 Redis 时验证入队与 SSE 流（可选）。"""

import asyncio
import json
import os
import queue
import threading
import uuid
from unittest.mock import MagicMock, patch

from src.services.stream_sse_filter import REPLAY_DISCARDED_EVENT_TYPES

# 测试中屏蔽 DB：任何真实 BaseTable 触发的连接直接报错（应通过 get_*_table mock 避免走到这里）
_DB_DISABLED_ERROR = RuntimeError("DB disabled in test (use mock tables only)")


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


async def _check_quota_noop(user_id: str):
    from src.services.quota_service import QuotaStatus

    return QuotaStatus(remaining_yuan=10.0, reset_at=None)


def _decode_sse_payload(frame: str) -> dict:
    return json.loads(frame.split("data: ", 1)[1].strip())


def _send_stream_job(
    *,
    session_id: str = "sess-1",
    task_id: str = "task-1",
    invocation_id: str = "inv-1",
    prompt: str = "new question",
    mode: str = "direct",
    **overrides,
) -> dict:
    job = {
        "session_id": session_id,
        "task_id": task_id,
        "invocation_id": invocation_id,
        "user_prompt": prompt,
        "mode": mode,
        "llm": None,
        "model": None,
        "byok_credential_id": None,
        "turn_input": {
            "user_text": prompt,
            "files": [],
            "images": [],
            "image_detail": None,
            "workspace_paths": [],
            "pre_turn_history_event_id": 0,
        },
        "images": [],
        "bohrium_required": False,
        "bohrium_submit_confirmation_required": None,
        "bohrium_job_max_runtime_seconds": None,
        "bohrium_node_sku_id": None,
        "workspace": None,
        "origin": None,
        "delivery": None,
        "submitted_at": "2026-06-04T00:00:00+00:00",
    }
    job.update(overrides)
    return job


async def _collect_n_frames(gen, n: int) -> list[dict]:
    """按帧取前 n 帧：历史回放会把多条 SSE 帧合并到一次 yield，这里按 \\n\\n 切回单帧。"""
    frames: list[dict] = []
    pending: list[str] = []
    while len(frames) < n:
        if not pending:
            chunk = await gen.__anext__()
            pending = [part for part in chunk.split("\n\n") if part.strip()]
        frames.append(_decode_sse_payload(pending.pop(0)))
    return frames


def test_chat_stream_returns_503_when_redis_url_missing(tmp_path):
    """无 REDIS_URL 时 POST /stream 返回 503（仅 Worker 队列模式，发送需 Redis）。"""
    mock_sessions = _mock_sessions_table()
    mock_events = _mock_events_table()

    patches = [
        patch("src.apis.chat_api.REDIS_URL", None),
        patch.dict(os.environ, {"LOG_DIR": str(tmp_path / "logs")}),
        patch(
            "src.base.base_table.BaseTable.get_connection",
            side_effect=lambda self: _NoDbConnection(),
        ),
        patch(
            "src.services.sessions_service.get_chat_sessions_table",
            return_value=mock_sessions,
        ),
        patch(
            "src.services.events_service.get_chat_events_table",
            return_value=mock_events,
        ),
        patch(
            "src.dao.chat_sessions_table.get_chat_sessions_table",
            return_value=mock_sessions,
        ),
        patch(
            "src.dao.chat_events_table.get_chat_events_table",
            return_value=mock_events,
        ),
        patch("src.apis.chat_api.check_quota_status", side_effect=_check_quota_noop),
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
        session_id = f"test-stream-503-{uuid.uuid4().hex[:12]}"
        url = f"/api/v1/chat/sessions/{session_id}/stream"
        headers = {"X-User-Id": "test-user-3656033"}
        body = {"content": "hello", "mode": "direct"}

        response = client.post(url, json=body, headers=headers)
        assert response.status_code == 503, response.text
        data = response.json()
        assert "队列" in data.get("msg", "") or "REDIS" in data.get("msg", "")
    finally:
        for p in patches:
            p.stop()


def test_prepare_send_message_captures_turn_input_before_user_event():
    from src.models.chat import ChatSendRequest
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.get_session.return_value = {
        "session_directory": None,
        "bohrium_submit_confirmation_required": False,
    }
    sessions_service.try_acquire_session_run.return_value = (True, None)
    events_service = MagicMock()
    events_service.get_latest_scope_event_id.return_value = 77
    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=MagicMock(),
    )
    req = ChatSendRequest(
        content="analyze current",
        files=["https://oss.example.com/chat/new.cif"],
        images=["https://oss.example.com/chat/current.png"],
        workspace_paths=["/share/current/POSCAR"],
        atom_selections=[
            {
                "id": "sel-1",
                "source_label": "POSCAR",
                "source_path": "/share/current/POSCAR",
                "source_format": "vasp",
                "atoms": [
                    {"order": 1, "element": "C", "cart_coord": [1.0, 2.0, 3.0]},
                ],
            }
        ],
    )

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=MagicMock()),
    ):
        ctx = service.prepare_send_message("sess-1", req, user_id="user-1")

    assert ctx.job["turn_input"]["user_text"] == "analyze current"
    assert ctx.job["turn_input"]["instruction_tag"] == "current-instruction"
    assert ctx.job["turn_input"]["files"] == ["https://oss.example.com/chat/new.cif"]
    assert ctx.job["turn_input"]["images"] == [
        "https://oss.example.com/chat/current.png"
    ]
    assert ctx.job["turn_input"]["workspace_paths"] == ["/share/current/POSCAR"]
    assert ctx.job["turn_input"]["atom_selections"] == [
        {
            "id": "sel-1",
            "source_label": "POSCAR",
            "source_path": "/share/current/POSCAR",
            "source_format": "vasp",
            "atoms": [
                {"order": 1, "element": "C", "cart_coord": [1.0, 2.0, 3.0]},
            ],
        }
    ]
    assert ctx.job["turn_input"]["pre_turn_history_event_id"] == 77
    assert ctx.user_msg["content"] == "analyze current"
    assert ctx.user_msg["atom_selections"] == ctx.job["turn_input"]["atom_selections"]
    assert "schema_version" not in ctx.user_msg
    events_service.get_latest_scope_event_id.assert_called_once_with("sess-1", None)
    events_service.add_history_event.assert_called_once()


def test_prepare_send_message_persists_and_passes_submit_confirmation():
    from src.models.chat import ChatSendRequest
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.get_session.return_value = {"session_directory": None}
    sessions_service.try_acquire_session_run.return_value = (True, None)
    events_service = MagicMock()
    events_service.get_latest_scope_event_id.return_value = 0
    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=MagicMock(),
    )
    req = ChatSendRequest(
        content="run",
        bohrium_submit_confirmation_required=False,
    )

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=MagicMock()),
    ):
        ctx = service.prepare_send_message("sess-1", req, user_id="user-1")

    assert ctx is not None
    assert ctx.job["bohrium_submit_confirmation_required"] is False
    sessions_service.set_bohrium_submit_confirmation.assert_called_once_with(
        "sess-1",
        "user-1",
        False,
    )


def test_prepare_send_message_uses_session_submit_confirmation_when_request_omits():
    from src.models.chat import ChatSendRequest
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.get_session.return_value = {
        "session_directory": None,
        "bohrium_submit_confirmation_required": 1,
    }
    sessions_service.try_acquire_session_run.return_value = (True, None)
    events_service = MagicMock()
    events_service.get_latest_scope_event_id.return_value = 0
    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=MagicMock(),
    )
    req = ChatSendRequest(content="run")

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=MagicMock()),
    ):
        ctx = service.prepare_send_message("sess-1", req, user_id="user-1")

    assert ctx is not None
    assert ctx.job["bohrium_submit_confirmation_required"] is True
    sessions_service.set_bohrium_submit_confirmation.assert_not_called()


def test_generate_send_stream_skips_current_task_in_history_replay():
    """发送流回放历史时不应再次回放当前任务刚落库的 query。"""
    from src.services.stream_service import ChatStreamService, SendStreamContext

    sessions_service = MagicMock()
    sessions_service.get_session_status_payload.return_value = {
        "source": "System",
        "type": "status",
        "content": "",
        "session_id": "sess-1",
    }
    events_service = MagicMock()
    events_service.get_session_events.return_value = [
        {
            "source": "User",
            "type": "query",
            "content": "old question",
            "session_id": "sess-1",
            "task_id": "task-0",
        },
        {
            "source": "MatMaster",
            "type": "run_result",
            "content": "old answer",
            "session_id": "sess-1",
            "task_id": "task-0",
        },
        {
            "source": "System",
            "type": "stream_closed",
            "content": "",
            "session_id": "sess-1",
            "task_id": "task-0",
        },
        {
            "source": "User",
            "type": "query",
            "content": "new question",
            "session_id": "sess-1",
            "task_id": "task-1",
            "invocation_id": "inv-1",
        },
    ]
    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=MagicMock(),
    )

    async def _collect_first_five_frames() -> list[dict]:
        ctx = SendStreamContext(
            task_id="task-1",
            invocation_id="inv-1",
            mode="direct",
            user_msg={
                "source": "User",
                "type": "query",
                "content": "new question",
                "mode": "direct",
                "session_id": "sess-1",
                "task_id": "task-1",
                "invocation_id": "inv-1",
            },
            job=_send_stream_job(),
        )
        gen = service.generate_send_stream("sess-1", ctx)
        try:
            return await _collect_n_frames(gen, 5)
        finally:
            await gen.aclose()

    with patch("src.services.stream_service.notify_post_async"):
        frames = asyncio.run(_collect_first_five_frames())

    assert [frame["type"] for frame in frames] == [
        "status",
        "query",
        "run_result",
        "stream_closed",
        "query",
    ]
    assert [frame["content"] for frame in frames[1:]] == [
        "old question",
        "old answer",
        "",
        "new question",
    ]
    assert frames[4]["type"] == "query"
    assert frames[4]["mode"] == "direct"
    events_service.get_session_events.assert_called_with(
        "sess-1", include_spawn=True, exclude_types=REPLAY_DISCARDED_EVENT_TYPES
    )


def test_prepare_send_message_marks_explicit_bohrium_requirement():
    from src.models.chat import ChatSendRequest
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.get_session.return_value = {"session_directory": None}
    sessions_service.try_acquire_session_run.return_value = (True, None)
    events_service = MagicMock()
    deploy_state_service = MagicMock()
    fake_redis = MagicMock()

    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=deploy_state_service,
    )

    req = ChatSendRequest(content="run", bohrium_project_id=42)

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=fake_redis),
    ):
        ctx = service.prepare_send_message(
            "sess-1",
            req,
            user_id="user-1",
            org_id="org-1",
        )

    assert ctx is not None
    assert ctx.job["bohrium_required"] is True


def test_prepare_send_message_persists_images_in_user_message():
    from src.models.chat import ChatSendRequest
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.get_session.return_value = {"session_directory": None}
    sessions_service.try_acquire_session_run.return_value = (True, None)
    events_service = MagicMock()
    deploy_state_service = MagicMock()
    fake_redis = MagicMock()

    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=deploy_state_service,
    )

    req = ChatSendRequest(
        content="看图",
        images=["https://oss.example.com/chat/a.png"],
    )

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=fake_redis),
    ):
        ctx = service.prepare_send_message("sess-1", req, user_id="user-1")

    assert ctx is not None
    assert ctx.user_msg["images"] == ["https://oss.example.com/chat/a.png"]
    assert ctx.job["images"] == ["https://oss.example.com/chat/a.png"]
    assert events_service.add_history_event.call_args.args[1]["images"] == [
        "https://oss.example.com/chat/a.png"
    ]


async def test_sse_frames_match_frontend_contract_without_mysql():
    """无需 MySQL，直接验证最终 SSE frame 的 payload shape 可被前端消费。"""
    from matmaster.integration.sse_handler import SSEHandler
    from matmaster.types.events import (
        BohriumNodeEvent,
        ErrorEvent,
        McpConnectEvent,
        McpServerStatusEvent,
        ResponseEvent,
        RunResultEvent,
        StreamClosedEvent,
        ToolCallEvent,
        ToolResultEvent,
    )
    from src.services.stream_service import ChatStreamService

    payloads = []

    async def collect_cb(payload):
        payloads.append(payload)

    handler = SSEHandler(
        send_cb=collect_cb,
        session_id="sess-verify",
        task_id="task-verify",
        invocation_id="inv-verify",
        mode="direct",
    )

    events = [
        ToolCallEvent(
            source="Agent",
            call_id="call-1",
            tool_name="bash",
            arguments={"cmd": "ls"},
        ),
        ToolResultEvent(
            source="Agent",
            call_id="call-1",
            tool_name="bash",
            result={"status": "success", "stdout": "ok"},
            payload={"auto_save": True},
        ),
        ErrorEvent(source="System", message="boom", traceback="tb"),
        BohriumNodeEvent(
            source="BohriumSetup",
            payload={
                "type": "setup_ready",
                "content": {
                    "status": "ready",
                    "message": "Node ready",
                    "node_id": 1,
                },
                "phase": "ssh",
            },
        ),
        McpServerStatusEvent(
            source="System",
            server_name="code-server",
            transport="sse",
            phase="retrying",
            detail={
                "message": "retrying",
                "attempt": 2,
                "max_attempts": 3,
                "error": "timeout",
            },
        ),
        McpConnectEvent(
            source="System",
            phase="ready",
            message="connected",
            elapsed_ms=123,
        ),
        ResponseEvent(source="Agent", content="done"),
        RunResultEvent(source="Agent", reason="natural", final_content="done"),
        StreamClosedEvent(source="System", task_completed=True, end_reason="natural"),
    ]

    for event in events:
        await handler.handle(event)

    frames = []
    for payload in payloads:
        frame = ChatStreamService.sse_format(payload)
        assert frame.startswith("event: ag-ui\n")
        frames.append(_decode_sse_payload(frame))

    assert [frame["type"] for frame in frames] == [
        "tool_call",
        "tool_result",
        "error",
        "bohrium_node",
        "mcp_server_status",
        "mcp_connect",
        "response",
        "run_result",
        "stream_closed",
    ]
    assert all(isinstance(frame.get("timestamp"), str) for frame in frames)

    assert frames[0]["content"] == {
        "id": "call-1",
        "call_id": "call-1",
        "name": "bash",
        "args": {"cmd": "ls"},
    }
    assert frames[1]["content"] == {
        "id": "call-1",
        "call_id": "call-1",
        "name": "bash",
        "result": {"status": "success", "stdout": "ok"},
        "status": "success",
        "info": {"auto_save": True},
    }
    assert frames[2]["content"] == {"message": "boom", "traceback": "tb"}
    assert frames[3]["content"] == {
        "status": "ready",
        "message": "Node ready",
        "node_id": 1,
        "phase": "ssh",
        "event_type": "setup_ready",
    }
    assert frames[4]["content"] == {
        "server_name": "code-server",
        "transport": "sse",
        "phase": "retrying",
        "message": "retrying",
        "attempt": 2,
        "max_attempts": 3,
        "error": "timeout",
    }
    assert frames[5]["content"] == {
        "phase": "ready",
        "message": "connected",
        "elapsed_ms": 123,
        "error": None,
    }
    assert frames[6]["content"] == "done"
    assert frames[7]["content"]["content"] == "done"
    assert "final_content" not in frames[7]
    assert frames[8]["task_completed"] is True
    assert frames[8]["end_reason"] == "natural"


def test_generate_send_stream_normalizes_replayed_history_source():
    from src.services.stream_service import ChatStreamService, SendStreamContext

    sessions_service = MagicMock()
    sessions_service.get_session_status_payload.return_value = {
        "source": "System",
        "type": "status",
        "content": "",
        "session_id": "sess-1",
    }
    events_service = MagicMock()
    events_service.get_session_events.return_value = [
        {
            "source": "Planner",
            "type": "run_result",
            "content": "old answer",
            "session_id": "sess-1",
            "task_id": "task-0",
        }
    ]
    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=MagicMock(),
    )

    async def _collect_frames() -> list[dict]:
        ctx = SendStreamContext(
            task_id="task-1",
            invocation_id="inv-1",
            mode="direct",
            user_msg={
                "source": "User",
                "type": "query",
                "content": "new question",
                "mode": "direct",
                "session_id": "sess-1",
                "task_id": "task-1",
                "invocation_id": "inv-1",
            },
            job=_send_stream_job(),
        )
        gen = service.generate_send_stream("sess-1", ctx)
        try:
            return [
                _decode_sse_payload(await gen.__anext__()),
                _decode_sse_payload(await gen.__anext__()),
                _decode_sse_payload(await gen.__anext__()),
            ]
        finally:
            await gen.aclose()

    with patch("src.services.stream_service.notify_post_async"):
        frames = asyncio.run(_collect_frames())

    history_frames = [frame for frame in frames if frame["type"] == "run_result"]
    assert len(history_frames) == 1
    assert history_frames[0]["source"] == "MatMaster"
    assert history_frames[0]["content"] == "old answer"
    events_service.get_session_events.assert_called_with(
        "sess-1", include_spawn=True, exclude_types=REPLAY_DISCARDED_EVENT_TYPES
    )


def test_generate_send_stream_replay_prefers_run_result_over_response():
    from src.services.stream_service import ChatStreamService, SendStreamContext

    sessions_service = MagicMock()
    sessions_service.get_session_status_payload.return_value = {
        "source": "System",
        "type": "status",
        "content": "",
        "session_id": "sess-1",
    }
    events_service = MagicMock()
    events_service.get_session_events.return_value = [
        {
            "source": "User",
            "type": "query",
            "content": "old question",
            "session_id": "sess-1",
            "task_id": "task-0",
        },
        {
            "source": "MatMaster",
            "type": "response",
            "content": {
                "content": "old answer",
                "model": "provider/private-model",
            },
            "session_id": "sess-1",
            "task_id": "task-0",
        },
        {
            "source": "MatMaster",
            "type": "run_result",
            "content": {
                "content": "old answer",
                "status": "completed",
                "reason": "natural",
            },
            "session_id": "sess-1",
            "task_id": "task-0",
        },
    ]
    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=MagicMock(),
    )

    async def _collect_first_four_frames() -> list[dict]:
        ctx = SendStreamContext(
            task_id="task-1",
            invocation_id="inv-1",
            mode="direct",
            user_msg={
                "source": "User",
                "type": "query",
                "content": "new question",
                "mode": "direct",
                "session_id": "sess-1",
                "task_id": "task-1",
                "invocation_id": "inv-1",
            },
            job=_send_stream_job(),
        )
        gen = service.generate_send_stream("sess-1", ctx)
        try:
            return await _collect_n_frames(gen, 4)
        finally:
            await gen.aclose()

    with patch("src.services.stream_service.notify_post_async"):
        frames = asyncio.run(_collect_first_four_frames())

    assert [frame["type"] for frame in frames] == [
        "status",
        "query",
        "run_result",
        "query",
    ]
    assert frames[2]["content"]["content"] == "old answer"
    assert frames[2]["content"]["status"] == "completed"
    assert frames[3]["content"] == "new question"
    events_service.get_session_events.assert_called_with(
        "sess-1", include_spawn=True, exclude_types=REPLAY_DISCARDED_EVENT_TYPES
    )


def test_generate_send_stream_replay_keeps_intermediate_response():
    """终态去重只隐藏最终答案副本：tool_call 前的中间 response 在刷新回放中保留。"""
    from src.services.stream_service import ChatStreamService, SendStreamContext

    sessions_service = MagicMock()
    sessions_service.get_session_status_payload.return_value = {
        "source": "System",
        "type": "status",
        "content": "",
        "session_id": "sess-1",
    }
    events_service = MagicMock()
    events_service.get_session_events.return_value = [
        {
            "source": "User",
            "type": "query",
            "content": "old question",
            "session_id": "sess-1",
            "task_id": "task-0",
        },
        {
            "source": "MatMaster",
            "type": "response",
            "content": {"content": "let me check the files", "turn_index": 0},
            "session_id": "sess-1",
            "task_id": "task-0",
        },
        {
            "source": "MatMaster",
            "type": "tool_call",
            "content": {"id": "c1", "call_id": "c1", "name": "bash", "args": {}},
            "session_id": "sess-1",
            "task_id": "task-0",
        },
        {
            "source": "MatMaster",
            "type": "tool_result",
            "content": {
                "id": "c1",
                "call_id": "c1",
                "name": "bash",
                "result": "ok",
                "status": "success",
                "info": {},
            },
            "session_id": "sess-1",
            "task_id": "task-0",
        },
        {
            "source": "MatMaster",
            "type": "response",
            "content": {"content": "old answer", "turn_index": 1},
            "session_id": "sess-1",
            "task_id": "task-0",
        },
        {
            "source": "MatMaster",
            "type": "run_result",
            "content": {
                "content": "old answer",
                "status": "completed",
                "reason": "natural",
                "num_turns": 2,
            },
            "session_id": "sess-1",
            "task_id": "task-0",
        },
    ]
    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=MagicMock(),
    )

    async def _collect_first_seven_frames() -> list[dict]:
        ctx = SendStreamContext(
            task_id="task-1",
            invocation_id="inv-1",
            mode="direct",
            user_msg={
                "source": "User",
                "type": "query",
                "content": "new question",
                "mode": "direct",
                "session_id": "sess-1",
                "task_id": "task-1",
                "invocation_id": "inv-1",
            },
            job=_send_stream_job(),
        )
        gen = service.generate_send_stream("sess-1", ctx)
        try:
            return await _collect_n_frames(gen, 7)
        finally:
            await gen.aclose()

    with patch("src.services.stream_service.notify_post_async"):
        frames = asyncio.run(_collect_first_seven_frames())

    assert [frame["type"] for frame in frames] == [
        "status",
        "query",
        "response",
        "tool_call",
        "tool_result",
        "run_result",
        "query",
    ]
    assert frames[2]["content"] == "let me check the files"
    assert frames[5]["content"]["content"] == "old answer"
    assert frames[6]["content"] == "new question"


def test_generate_send_stream_subscribes_before_enqueue():
    from src.services.stream_service import ChatStreamService, SendStreamContext

    sessions_service = MagicMock()
    sessions_service.get_session_status_payload.return_value = {
        "source": "System",
        "type": "status",
        "content": "",
        "session_id": "sess-1",
    }
    sessions_service.get_session_user_id.return_value = "user-1"
    events_service = MagicMock()
    events_service.get_session_events.return_value = []
    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=MagicMock(),
    )

    subscribe_ready = threading.Event()
    published = queue.Queue()
    call_order: list[str] = []

    class _FakePubSub:
        def subscribe(self, _channel: str) -> None:
            return None

        def get_message(self, timeout: float = 1.0):
            if not subscribe_ready.is_set():
                subscribe_ready.set()
                call_order.append("subscribe")
                return {"type": "subscribe", "data": 1}
            try:
                return published.get(timeout=timeout)
            except queue.Empty:
                return None

        def unsubscribe(self, _channel: str) -> None:
            return None

        def close(self) -> None:
            return None

    class _FakeClient:
        def pubsub(self) -> _FakePubSub:
            return _FakePubSub()

    fake_redis = MagicMock()
    fake_redis.create_client.return_value = _FakeClient()
    fake_redis.set_session_run_queued.return_value = True
    fake_redis.llen_agent_run_queue.return_value = 0

    def _lpush_agent_run_job(_job: dict) -> bool:
        call_order.append("lpush")
        assert subscribe_ready.is_set()
        removed_context_key = "current_input" "_context"
        removed_boundary_key = "pre_query" "_scope_event_id"
        assert "turn_input" in _job
        assert removed_context_key not in _job
        assert removed_boundary_key not in json.dumps(_job, ensure_ascii=False)
        published.put(
            {
                "type": "message",
                "data": json.dumps(
                    {
                        "source": "System",
                        "type": "stream_closed",
                        "content": "",
                        "session_id": "sess-1",
                        "invocation_id": "inv-1",
                    }
                ),
            }
        )
        return True

    fake_redis.lpush_agent_run_job.side_effect = _lpush_agent_run_job

    async def _collect_frames() -> list[dict]:
        ctx = SendStreamContext(
            task_id="task-1",
            invocation_id="inv-1",
            mode="direct",
            user_msg={
                "source": "User",
                "type": "query",
                "content": "new question",
                "mode": "direct",
                "session_id": "sess-1",
                "task_id": "task-1",
                "invocation_id": "inv-1",
            },
            job=_send_stream_job(),
        )
        gen = service.generate_send_stream("sess-1", ctx)
        try:
            return [
                _decode_sse_payload(await gen.__anext__()),
                _decode_sse_payload(await gen.__anext__()),
                _decode_sse_payload(await gen.__anext__()),
            ]
        finally:
            await gen.aclose()

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=fake_redis),
        patch("src.services.stream_service.notify_post_async"),
        patch(
            "src.services.stream_service.UserService.get_user_info_for_display",
            return_value={
                "user_id": "user-1",
                "nickname": "Tester",
                "email": "tester@example.com",
            },
        ),
        patch(
            "src.services.stream_service.get_worker_registry_service",
            return_value=MagicMock(count_active_runs=MagicMock(return_value=0)),
        ),
    ):
        frames = asyncio.run(_collect_frames())

    assert [frame["type"] for frame in frames] == ["status", "query", "stream_closed"]
    assert call_order[:2] == ["subscribe", "lpush"]
