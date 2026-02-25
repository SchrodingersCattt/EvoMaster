"""Chat 流式接口业务逻辑：SSE 队列管理、仅订阅流、发送消息流。"""

import asyncio
import json
import logging
import queue
import threading
import uuid
from dataclasses import dataclass
from typing import AsyncGenerator

from src.models.chat import ChatSendRequest
from src.services import chat_service as svc
from src.utils.constant import AG_UI_EVENT

logger = logging.getLogger(__name__)

# session_id -> 该会话下所有 SSE 连接的队列，agent 事件会广播到这些队列
_sse_queues: dict[str, list[asyncio.Queue]] = {}
# session_id -> 当前 run 的 planner_reply 队列（POST /planner_reply 写入）
_planner_reply_queues: dict[str, queue.Queue] = {}


def sse_format(payload: dict) -> str:
    """ag-ui 协议：单条 SSE 格式为 event: ag-ui\\ndata: {json}\\n\\n"""
    return f"event: {AG_UI_EVENT}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def get_planner_reply_queue(session_id: str) -> queue.Queue | None:
    """供 POST /planner_reply 等写入使用。"""
    return _planner_reply_queues.get(session_id)


def broadcast_to_sse_queues(session_id: str, payload: dict) -> None:
    """向该会话下所有订阅队列广播一条消息。"""
    for q in _sse_queues.get(session_id) or []:
        try:
            q.put_nowait(payload)
        except Exception:
            pass


@dataclass
class SendStreamContext:
    """发送消息流所需上下文，由 prepare_send_message 返回。"""

    task_id: str
    mode: str
    user_msg: dict
    request_event_queue: asyncio.Queue
    planner_reply_queue: queue.Queue
    stop_ev: threading.Event


async def generate_subscribe_stream(session_id: str) -> AsyncGenerator[str, None]:
    """
    仅订阅模式：先推送历史事件（不含 log_line），再注册到 _sse_queues，然后循环等待新事件或 30s ping。
    在 generator 内部完成队列的注册与注销。
    """
    sid = session_id.strip()
    event_queue: asyncio.Queue = asyncio.Queue()
    if sid not in _sse_queues:
        _sse_queues[sid] = []
    _sse_queues[sid].append(event_queue)
    try:
        events = svc.get_session_events(sid)
        if events:
            for event in events:
                if event.get('type') != 'log_line':
                    yield sse_format(event)
        while True:
            try:
                payload = await asyncio.wait_for(event_queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                yield sse_format(
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
                yield sse_format(payload)
                break
            yield sse_format(payload)
    finally:
        if sid in _sse_queues:
            try:
                _sse_queues[sid].remove(event_queue)
            except ValueError:
                pass
            if not _sse_queues[sid]:
                del _sse_queues[sid]


def prepare_send_message(
    session_id: str,
    req: ChatSendRequest,
    user_id: str | None,
    org_id: str | None = None,
) -> SendStreamContext | None:
    """
    为发送消息做准备：确保会话、尝试占用 run、更新 Bohrium 凭证、写入用户消息、创建队列与 stop_ev。
    若该会话已有任务在运行则返回 None（调用方应返回 409）。org_id 由上游 Header X-Org-Id 注入。
    """
    sid = session_id.strip()
    svc.ensure_session(sid, user_id=user_id)
    if not svc.try_acquire_session_run(sid):
        return None

    mode = (req.mode or 'direct').strip().lower() or 'direct'
    if mode not in ('direct', 'planner'):
        mode = 'direct'

    if (
        req.bohrium_access_key
        or req.bohrium_project_id
        or org_id is not None
        or req.bohrium_user_id is not None
    ):
        bohrium_creds = svc.SESSIONS[sid].get('bohrium_credentials') or {}
        bohrium_creds = dict(bohrium_creds)
        if req.bohrium_access_key:
            bohrium_creds['access_key'] = req.bohrium_access_key.strip()
        if req.bohrium_project_id is not None:
            try:
                bohrium_creds['project_id'] = int(req.bohrium_project_id)
            except (TypeError, ValueError):
                pass
        if org_id is not None:
            bohrium_creds['org_id'] = str(org_id).strip()
        if req.bohrium_user_id is not None:
            bohrium_creds['user_id'] = (
                req.bohrium_user_id
                if isinstance(req.bohrium_user_id, str)
                else int(req.bohrium_user_id)
            )
        if bohrium_creds:
            svc.SESSIONS[sid]['bohrium_credentials'] = bohrium_creds

    task_id = 'sse_' + uuid.uuid4().hex[:8]
    svc.set_session_last_task(sid, task_id, user_id=user_id)
    user_prompt = (req.content or '').strip()
    user_msg = {
        'source': 'User',
        'type': 'query',
        'content': user_prompt,
        'mode': mode,
        'session_id': sid,
    }
    svc.add_history_event(sid, user_msg, user_id=user_id)

    request_event_queue: asyncio.Queue = asyncio.Queue()
    planner_reply_queue: queue.Queue = queue.Queue()
    _planner_reply_queues[sid] = planner_reply_queue
    stop_ev = threading.Event()
    svc.set_stop_event(sid, stop_ev)

    return SendStreamContext(
        task_id=task_id,
        mode=mode,
        user_msg=user_msg,
        request_event_queue=request_event_queue,
        planner_reply_queue=planner_reply_queue,
        stop_ev=stop_ev,
    )


def _send_cb(
    session_id: str,
    request_event_queue: asyncio.Queue,
    payload: dict,
) -> None:
    """供 run_agent_sync 的 send_cb 使用：写入本连接队列并广播到订阅队列。"""
    request_event_queue.put_nowait(payload)
    broadcast_to_sse_queues(session_id, payload)


async def generate_send_stream(
    session_id: str,
    user_prompt: str,
    ctx: SendStreamContext,
) -> AsyncGenerator[str, None]:
    """
    发送消息流：先推送历史 + 用户消息 + 状态，再在后台跑 agent，本连接从 request_event_queue 收事件并 yield。
    """
    sid = session_id.strip()
    mode = ctx.mode
    loop = asyncio.get_event_loop()

    for event in svc.get_session_events(sid) or []:
        if event.get('type') != 'log_line':
            yield sse_format(event)
    yield sse_format(ctx.user_msg)
    yield sse_format(
        {
            'source': 'System',
            'type': 'status',
            'content': f"Initializing ({mode})...",
            'session_id': sid,
        }
    )

    async def send_cb(payload: dict):
        _send_cb(sid, ctx.request_event_queue, payload)

    future = loop.run_in_executor(
        svc.get_executor(),
        svc.run_agent_sync,
        sid,
        user_prompt,
        send_cb,
        loop,
        ctx.stop_ev,
        mode,
        ctx.planner_reply_queue,
        ctx.task_id,
    )
    while True:
        payload = await ctx.request_event_queue.get()
        yield sse_format(payload)
        if payload.get('type') == 'end':
            break
    await future
