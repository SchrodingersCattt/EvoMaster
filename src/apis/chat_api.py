"""Chat 相关的 API

包含：
1. Sessions 列表相关的接口（不需要 session_id）：
   - POST /chat/sessions/start - 创建新会话
   - GET /chat/sessions/list - 获取会话列表
   - GET /chat/sessions/api/runs - 获取 runs 列表
   - GET /chat/sessions/api/runs/{run_id}/files - 获取 run 的文件列表

2. 单个 Session 相关的接口（需要 session_id）：
   - POST /chat/sessions/{session_id}/stream - 统一流接口：body 不传或 content 为空→历史+ping；有 content→发送消息并返回本次 SSE 流
   - POST /chat/sessions/{session_id}/cancel - 取消运行
   - POST /chat/sessions/{session_id}/planner_reply - Planner 回复
   - GET /chat/sessions/{session_id}/history - 获取历史
   - GET /chat/sessions/{session_id}/run_info - 获取运行信息
   - GET /chat/sessions/{session_id}/files - 获取文件列表
   - GET /chat/sessions/{session_id}/files/content - 获取文件内容
   - GET /chat/sessions/{session_id}/api/share - 获取分享数据
"""

import asyncio
import json
import queue
import threading
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from src.models.chat import (
    ChatPlannerReplyRequest,
    ChatRequest,
    ChatSendRequest,
    ChatStartResponse,
    FileEntry,
    RunFilesResponse,
    RunInfoResponse,
    RunItem,
    RunListResponse,
    SessionFilesResponse,
    SessionItem,
    SessionListResponse,
)
from src.services import chat_service as svc
from src.utils.user import get_current_user

router = APIRouter()

# ag-ui：session_id -> 该会话下所有 SSE 连接的队列，agent 事件会广播到这些队列
_sse_queues: dict[str, list[asyncio.Queue]] = {}
# session_id -> 当前 run 的 planner_reply 队列（POST /planner_reply 写入）
_planner_reply_queues: dict[str, queue.Queue] = {}

AG_UI_EVENT = 'ag-ui'


def get_user_id(request: Request) -> str:
    """获取必需的 user_id（如果获取不到则抛出异常）"""
    user_context = get_current_user(request)
    return user_context.user_id


def _sse_format(payload: dict) -> str:
    """ag-ui 协议：单条 SSE 格式为 event: ag-ui\\ndata: {json}\\n\\n"""
    return f"event: {AG_UI_EVENT}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ========== Sessions 列表相关的接口（不需要 session_id） ==========


@router.post('/start', response_model=ChatStartResponse)
async def start_task(req: ChatRequest, user_id: str = Depends(get_user_id)):
    """创建新会话"""
    session_id = svc.start_session(user_id=user_id)
    return ChatStartResponse(status='ready', session_id=session_id)


@router.get('/list', response_model=SessionListResponse)
def list_sessions(user_id: str = Depends(get_user_id)):
    """获取会话列表（需要用户认证）"""
    sessions = svc.get_sessions(user_id=user_id)
    return SessionListResponse(sessions=[SessionItem(**s) for s in sessions])


@router.get('/api/runs', response_model=RunListResponse)
def list_runs():
    """获取 runs 列表"""
    runs = svc.list_runs()
    return RunListResponse(runs=[RunItem(**r) for r in runs])


@router.get('/api/runs/{run_id}/files', response_model=RunFilesResponse)
def list_run_files(run_id: str, path: str = '', task_id: str | None = None):
    """获取 run 的文件列表"""
    result = svc.list_run_files(run_id, path, task_id)
    if result is None:
        raise HTTPException(status_code=404, detail='Run not found')
    entries = [FileEntry(**e) for e in result['entries']]
    return RunFilesResponse(
        run_id=result['run_id'],
        path=result['path'],
        entries=entries,
    )


# ========== 单个 Session 相关的接口（需要 session_id） ==========


@router.post('/{session_id}/stream')
async def chat_stream(
    session_id: str,
    req: ChatSendRequest | None = Body(None),
    user_id: str = Depends(get_user_id),
):
    """ag-ui：统一流接口。body 不传或 content 为空→仅历史+ping；有 content→发送消息并返回本次运行的 SSE 流（多 worker 一致）。"""
    sid = session_id.strip() or svc.SESSION_ID_DEMO
    svc.ensure_session(sid, user_id=user_id)
    user_prompt = (req.content or '').strip() if req else ''
    subscribe_only = not user_prompt

    if subscribe_only:
        # 仅订阅：历史 + 注册到 _sse_queues + ping
        event_queue: asyncio.Queue = asyncio.Queue()
        if sid not in _sse_queues:
            _sse_queues[sid] = []
        _sse_queues[sid].append(event_queue)
        try:

            async def generate_subscribe():
                try:
                    events = svc.get_session_events(sid)
                    if events:
                        for event in events:
                            if event.get('type') != 'log_line':
                                yield _sse_format(event)
                    while True:
                        try:
                            payload = await asyncio.wait_for(
                                event_queue.get(), timeout=30.0
                            )
                        except asyncio.TimeoutError:
                            yield _sse_format(
                                {
                                    'source': 'System',
                                    'type': 'ping',
                                    'content': '',
                                    'session_id': sid,
                                }
                            )
                            continue
                        if payload is None:
                            break
                        if payload.get('type') == 'end':
                            yield _sse_format(payload)
                            break
                        yield _sse_format(payload)
                finally:
                    if sid in _sse_queues:
                        try:
                            _sse_queues[sid].remove(event_queue)
                        except ValueError:
                            pass
                        if not _sse_queues[sid]:
                            del _sse_queues[sid]

            return StreamingResponse(
                generate_subscribe(),
                media_type='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                    'X-Accel-Buffering': 'no',
                },
            )
        except Exception:
            if sid in _sse_queues:
                try:
                    _sse_queues[sid].remove(event_queue)
                except ValueError:
                    pass
                if not _sse_queues[sid]:
                    del _sse_queues[sid]
            raise

    # 发送消息并返回本次运行的 SSE 流（此时 req 必存在且 content 非空）
    assert req is not None
    mode = (req.mode or 'direct').strip().lower() or 'direct'
    if mode not in ('direct', 'planner'):
        mode = 'direct'
    sid = session_id.strip()
    svc.ensure_session(sid, user_id=user_id)

    if req.bohrium_access_key or req.bohrium_project_id:
        bohrium_creds = {}
        if req.bohrium_access_key:
            bohrium_creds['access_key'] = req.bohrium_access_key.strip()
        if req.bohrium_project_id is not None:
            try:
                bohrium_creds['project_id'] = int(req.bohrium_project_id)
            except (TypeError, ValueError):
                pass
        if bohrium_creds:
            svc.SESSIONS[sid]['bohrium_credentials'] = bohrium_creds

    task_id = 'sse_' + uuid.uuid4().hex[:8]
    svc.set_session_last_task(sid, task_id, user_id=user_id)
    user_msg = {
        'source': 'User',
        'type': 'query',
        'content': user_prompt,
        'mode': mode,
        'session_id': sid,
    }
    svc.add_history_event(sid, user_msg, user_id=user_id)

    loop = asyncio.get_event_loop()
    # 本请求专属队列，保证多 worker 下流与请求在同一连接
    request_event_queue: asyncio.Queue = asyncio.Queue()

    async def send_cb(payload: dict):
        request_event_queue.put_nowait(payload)
        for q in _sse_queues.get(sid) or []:
            try:
                q.put_nowait(payload)
            except Exception:
                pass

    planner_reply_queue: queue.Queue = queue.Queue()
    _planner_reply_queues[sid] = planner_reply_queue
    stop_ev = threading.Event()
    svc.set_stop_event(sid, stop_ev)

    init_msg = {
        'source': 'System',
        'type': 'status',
        'content': f"Initializing ({mode})...",
        'session_id': sid,
    }

    async def generate():
        # 1) 历史（不含 log_line）
        for event in svc.get_session_events(sid) or []:
            if event.get('type') != 'log_line':
                yield _sse_format(event)
        # 2) 用户消息 + 状态
        yield _sse_format(user_msg)
        yield _sse_format(init_msg)
        # 3) 在后台跑 agent，本连接从 request_event_queue 收事件
        future = loop.run_in_executor(
            svc.get_executor(),
            svc.run_agent_sync,
            sid,
            user_prompt,
            send_cb,
            loop,
            stop_ev,
            mode,
            planner_reply_queue,
            task_id,
        )
        while True:
            payload = await request_event_queue.get()
            yield _sse_format(payload)
            if payload.get('type') == 'end':
                break
        await future

    return StreamingResponse(
        generate(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        },
    )


@router.post('/{session_id}/cancel')
async def chat_cancel(session_id: str):
    """ag-ui：取消当前会话的运行。"""
    svc.cancel_run(session_id)
    return {'session_id': session_id, 'status': 'cancelled'}


@router.post('/{session_id}/planner_reply')
async def chat_planner_reply(
    session_id: str, req: ChatPlannerReplyRequest, user_id: str = Depends(get_user_id)
):
    """ag-ui：Planner 模式下用户对 planner_ask 的回复。"""
    svc.ensure_session(session_id, user_id=user_id)
    payload = {
        'source': 'User',
        'type': 'planner_reply',
        'content': req.content,
        'session_id': session_id,
    }
    svc.add_history_event(session_id, payload, user_id=user_id)
    pq = _planner_reply_queues.get(session_id)
    if pq is not None:
        pq.put(req.content)
    return {'session_id': session_id, 'status': 'ok'}


@router.get('/{session_id}/history')
def get_session_history(session_id: str):
    """获取会话历史消息"""
    return svc.get_session_events(session_id)


@router.get('/{session_id}/run_info', response_model=RunInfoResponse)
def get_session_run_info(session_id: str):
    """获取会话的运行信息"""
    return RunInfoResponse(**svc.get_session_run_info(session_id))


@router.get('/{session_id}/files', response_model=SessionFilesResponse)
def list_session_files(session_id: str, path: str = ''):
    """获取会话的文件列表"""
    result = svc.list_session_files(session_id, path)
    if result is None:
        raise HTTPException(status_code=404, detail='Path not found')
    entries = [FileEntry(**e) for e in result['entries']]
    return SessionFilesResponse(
        run_id=result['run_id'],
        path=result['path'],
        entries=entries,
        workspace_root=result.get('workspace_root'),
        task_id=result.get('task_id'),
    )


@router.get('/{session_id}/files/content')
def get_session_file_content(session_id: str, path: str):
    """获取会话文件内容"""
    if not path or not path.strip():
        raise HTTPException(status_code=400, detail='path is required')
    file_path, media_type = svc.get_session_file_path(session_id, path)
    if file_path is None:
        raise HTTPException(status_code=404, detail='File not found')
    return FileResponse(
        path=str(file_path),
        media_type=media_type or 'application/octet-stream',
        filename=file_path.name,
    )


@router.get('/{session_id}/api/share')
def get_share_data(session_id: str):
    """获取分享数据"""
    history = svc.get_share_history(session_id)
    if history is None:
        raise HTTPException(status_code=404, detail='Session not found')
    return history
