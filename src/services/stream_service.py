"""Chat 流式接口业务逻辑：SSE 队列管理、仅订阅流、发送消息流。"""

import asyncio
import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import AsyncGenerator

from src.models.chat import ChatSendRequest
from src.services import sessions_service as svc
from src.services.agent_run_service import (
    AgentRunService,
    get_agent_run_service,
)
from src.services.events_service import ChatEventsService, get_events_service
from src.services.sessions_service import ChatSessionsService, get_sessions_service
from src.utils.constant import AG_UI_EVENT

logger = logging.getLogger(__name__)


class StreamQueueManager:
    """流式接口的队列管理：SSE 订阅队列的注册/注销与广播；Planner 模式下 session -> planner_reply 队列。"""

    def __init__(self) -> None:
        # session_id -> 该会话下所有 SSE 连接的队列，agent 事件会广播到这些队列
        self._sse_queues: dict[str, list[asyncio.Queue]] = {}
        # session_id -> 当前 run 的 planner_reply 队列（POST /planner_reply 写入）
        self._planner_reply_queues: dict[str, queue.Queue] = {}

    def set_planner_reply_queue(self, session_id: str, q: queue.Queue) -> None:
        """注册该会话当前 run 的 planner_reply 队列。"""
        self._planner_reply_queues[session_id.strip()] = q

    def get_planner_reply_queue(self, session_id: str) -> queue.Queue | None:
        """供 POST /planner_reply 写入使用；无活跃 planner run 时返回 None。"""
        return self._planner_reply_queues.get(session_id.strip())

    def clear_planner_reply_queue(self, session_id: str) -> None:
        """run 结束后清除，避免后续误写入。"""
        self._planner_reply_queues.pop(session_id.strip(), None)

    def register_subscriber(self, session_id: str) -> asyncio.Queue:
        """为会话注册一个 SSE 订阅队列，返回该队列。"""
        sid = session_id.strip()
        event_queue: asyncio.Queue = asyncio.Queue()
        if sid not in self._sse_queues:
            self._sse_queues[sid] = []
        self._sse_queues[sid].append(event_queue)
        return event_queue

    def unregister_subscriber(
        self, session_id: str, event_queue: asyncio.Queue
    ) -> None:
        """从会话中注销一个 SSE 订阅队列。"""
        sid = session_id.strip()
        if sid not in self._sse_queues:
            return
        try:
            self._sse_queues[sid].remove(event_queue)
        except ValueError:
            pass
        if not self._sse_queues[sid]:
            del self._sse_queues[sid]

    def broadcast(self, session_id: str, payload: dict) -> None:
        """向该会话下所有订阅队列广播一条消息。"""
        for q in self._sse_queues.get(session_id.strip()) or []:
            try:
                q.put_nowait(payload)
            except Exception:
                pass


@dataclass
class SendStreamContext:
    """发送消息流所需上下文，由 prepare_send_message 返回。"""

    task_id: str
    invocation_id: str  # 本轮调用的唯一标识，前端用于区分第几轮
    mode: str
    user_msg: dict
    request_event_queue: asyncio.Queue
    planner_reply_queue: queue.Queue
    stop_ev: threading.Event


class ChatStreamService:
    """流式接口服务：仅订阅流、发送消息流。队列由 StreamQueueManager 管理。"""

    def __init__(
        self,
        queue_manager: StreamQueueManager | None = None,
        sessions_service: ChatSessionsService | None = None,
        events_service: ChatEventsService | None = None,
        agent_run_service: AgentRunService | None = None,
    ) -> None:
        self._queues = queue_manager or StreamQueueManager()
        self._sessions_service = sessions_service or get_sessions_service()
        self._events_service = events_service or get_events_service()
        self._agent_run_service = agent_run_service or get_agent_run_service()

    @staticmethod
    def sse_format(payload: dict) -> str:
        """ag-ui 协议：单条 SSE 格式为 event: ag-ui\\ndata: {json}\\n\\n"""
        return (
            f"event: {AG_UI_EVENT}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        )

    @staticmethod
    def _inject_elapsed_for_history(events: list[dict]) -> list[dict]:
        """为历史事件按 task_id 补全 stream_started_at、elapsed_ms，便于刷新后前端仍能展示耗时。"""
        task_start_ms: dict[str, int] = {}
        for ev in events:
            tid = ev.get('task_id')
            t_ms = ev.get('created_at_ms')
            if tid is not None and t_ms is not None:
                if tid not in task_start_ms or t_ms < task_start_ms[tid]:
                    task_start_ms[tid] = t_ms
        out = []
        for ev in events:
            ev = dict(ev)
            tid = ev.get('task_id')
            t_ms = ev.get('created_at_ms')
            if tid and t_ms is not None and tid in task_start_ms:
                start = task_start_ms[tid]
                ev['stream_started_at'] = start
                ev['elapsed_ms'] = t_ms - start
            out.append(ev)
        return out

    async def generate_subscribe_stream(
        self, session_id: str
    ) -> AsyncGenerator[str, None]:
        """
        仅订阅模式：先推送当前会话状态与历史事件，再注册到订阅队列。
        若本进程有该 session 的运行中任务，则循环等待新事件或 30s ping；若无运行中任务则发完历史后直接结束流，
        前端可据此（流结束）退出「运行中」状态。
        若 DB 为 active 但本进程未在跑该 session（部署/重启导致上一 run 在别的 pod 上被中断），
        则重置为 idle、推送 run_interrupted（原因：部署），并自动在新 pod 上重跑上次任务。
        """
        sid = session_id.strip()
        event_queue = self._queues.register_subscriber(sid)
        try:
            payload = self._sessions_service.get_session_status_payload(sid)
            # 部署/重启后：DB 仍为 active 但本进程没有该 session 的 run → 视为上一轮在别的 pod 上被中断
            if payload.get(
                'status'
            ) == 'active' and not self._sessions_service.is_session_running_on_this_pod(
                sid
            ):
                logger.info(
                    'run_interrupted: stale session detected (deploy/restart), '
                    'resetting to idle and will auto retry session_id=%s',
                    sid,
                )
                self._sessions_service.reset_session_status_to_idle_in_db(sid)
                payload = self._sessions_service.get_session_status_payload(sid)
                last_query = self._events_service.get_last_user_query(sid)
                yield self.sse_format(payload)
                run_interrupted_content = (
                    '上一轮任务因服务部署/重启中断，正在自动重新执行。'
                )
                last_user_content = (last_query or {}).get('content', '')
                run_interrupted_payload = {
                    'source': 'System',
                    'type': 'run_interrupted',
                    'content': run_interrupted_content,
                    'session_id': sid,
                    'reason': 'deploy',
                    'last_user_content': last_user_content,
                }
                yield self.sse_format(run_interrupted_payload)
                # 入库，便于历史/导出（如 CSV）中有重启记录；task_id 指向被中断的那一轮
                interrupted_task_id = payload.get('last_task_id')
                self._events_service.add_history_event(
                    sid,
                    {
                        'source': 'System',
                        'type': 'run_interrupted',
                        'content': {
                            'message': run_interrupted_content,
                            'reason': 'deploy',
                            'last_user_content': last_user_content,
                        },
                        'session_id': sid,
                        'task_id': interrupted_task_id,
                    },
                    user_id=self._sessions_service.get_session_user_id(sid),
                )
                # 在新 pod 上自动重跑上次用户输入
                if last_query and (last_query.get('content') or '').strip():
                    user_id = self._sessions_service.get_session_user_id(sid)
                    retry_req = ChatSendRequest(
                        content=(last_query.get('content') or '').strip(),
                        files=last_query.get('files') or None,
                        mode=last_query.get('mode') or 'direct',
                    )
                    ctx = self.prepare_send_message(
                        sid, retry_req, user_id, org_id=None
                    )
                    if ctx is not None:
                        loop = asyncio.get_event_loop()

                        def send_cb(p: dict) -> None:
                            self._queues.broadcast(sid, p)

                        loop.run_in_executor(
                            self._agent_run_service.get_executor(),
                            self._agent_run_service.run_agent_sync,
                            sid,
                            (last_query.get('content') or '').strip(),
                            send_cb,
                            loop,
                            ctx.stop_ev,
                            ctx.mode,
                            ctx.planner_reply_queue,
                            ctx.task_id,
                        )
                        logger.info(
                            'run_interrupted: auto retry started session_id=%s',
                            sid,
                        )
                    else:
                        logger.warning(
                            'run_interrupted: auto retry skipped (prepare_send_message '
                            'returned None, e.g. session already in run) session_id=%s',
                            sid,
                        )
                else:
                    logger.info(
                        'run_interrupted: no last user query to retry session_id=%s',
                        sid,
                    )
            else:
                yield self.sse_format(payload)
            events = self._events_service.get_session_events(sid)
            if events:
                events = self._inject_elapsed_for_history(events)
                for event in events:
                    if event.get('type') != 'log_line':
                        yield self.sse_format(event)
            # 若无运行中任务，发完历史后直接结束流，前端可据此退出「运行中」状态
            while self._sessions_service.is_session_running_on_this_pod(sid):
                try:
                    payload = await asyncio.wait_for(event_queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield self.sse_format(
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
                    yield self.sse_format(payload)
                    break
                yield self.sse_format(payload)
        finally:
            self._queues.unregister_subscriber(sid, event_queue)

    def prepare_send_message(
        self,
        session_id: str,
        req: ChatSendRequest,
        user_id: str | None,
        org_id: str | None = None,
    ) -> SendStreamContext | None:
        """
        为发送消息做准备：确保会话、尝试占用 run、更新 Bohrium 凭证、写入用户消息、创建队列与 stop_ev。
        若该会话已有任务在运行则返回 None（调用方应返回 409）。
        """
        sid = session_id.strip()
        self._sessions_service.ensure_session(sid, user_id=user_id)
        if not self._sessions_service.try_acquire_session_run(sid):
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

        task_id = 'sse_' + uuid.uuid4().hex[:16]
        invocation_id = 'inv_' + uuid.uuid4().hex[:16]
        self._sessions_service.set_session_last_task(sid, task_id, user_id=user_id)
        user_content = (req.content or '').strip()
        user_msg = {
            'source': 'User',
            'type': 'query',
            'content': user_content,
            'mode': mode,
            'session_id': sid,
            'task_id': task_id,
            'invocation_id': invocation_id,
        }
        if req.files:
            user_msg['files'] = list(req.files)
        self._events_service.add_history_event(sid, user_msg, user_id=user_id)

        request_event_queue: asyncio.Queue = asyncio.Queue()
        planner_reply_queue: queue.Queue = queue.Queue()
        stop_ev = threading.Event()
        self._sessions_service.set_stop_event(sid, stop_ev)
        self._queues.set_planner_reply_queue(sid, planner_reply_queue)

        return SendStreamContext(
            task_id=task_id,
            invocation_id=invocation_id,
            mode=mode,
            user_msg=user_msg,
            request_event_queue=request_event_queue,
            planner_reply_queue=planner_reply_queue,
            stop_ev=stop_ev,
        )

    def get_planner_reply_queue(self, session_id: str) -> queue.Queue | None:
        """供 POST /planner_reply 写入使用；无活跃 planner run 时返回 None。"""
        return self._queues.get_planner_reply_queue(session_id)

    def broadcast_planner_reply(self, session_id: str, content: str) -> None:
        """将用户 planner_reply 广播到该会话所有 SSE 订阅，便于前端展示。"""
        self._queues.broadcast(
            session_id,
            {
                'source': 'User',
                'type': 'planner_reply',
                'content': content,
                'session_id': session_id.strip(),
            },
        )

    def _send_cb(
        self,
        session_id: str,
        request_event_queue: asyncio.Queue,
        payload: dict,
    ) -> None:
        """供 run_agent_sync 的 send_cb 使用：写入本连接队列并广播到订阅队列。"""
        request_event_queue.put_nowait(payload)
        self._queues.broadcast(session_id, payload)

    async def generate_send_stream(
        self,
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
        start_time_ms = int(time.time() * 1000)
        logger.info(
            'generate_send_stream: start session_id=%s task_id=%s mode=%s',
            sid,
            ctx.task_id,
            mode,
        )

        # 流开头推送当前会话状态（含 last_task_id、invocation_id 等），便于前端区分轮次
        payload = self._sessions_service.get_session_status_payload(sid)
        payload['stream_started_at'] = start_time_ms
        payload['invocation_id'] = ctx.invocation_id
        yield self.sse_format(payload)
        history = self._events_service.get_session_events(sid) or []
        history = self._inject_elapsed_for_history(history)
        for event in history:
            if event.get('type') != 'log_line':
                yield self.sse_format(event)
        yield self.sse_format(ctx.user_msg)
        yield self.sse_format(
            {
                'source': 'System',
                'type': 'status',
                'content': f"Initializing ({mode})...",
                'session_id': sid,
                'stream_started_at': start_time_ms,
                'invocation_id': ctx.invocation_id,
            }
        )

        def send_cb(payload: dict):
            """同步回调：用 call_soon_threadsafe 把事件投递到 event loop，不等待。
            避免因 SSE 写阻塞导致 run_coroutine_threadsafe 超时、事件已落库但未推送到前端。"""
            loop.call_soon_threadsafe(
                self._send_cb, sid, ctx.request_event_queue, payload
            )

        future = loop.run_in_executor(
            self._agent_run_service.get_executor(),
            self._agent_run_service.run_agent_sync,
            sid,
            user_prompt,
            send_cb,
            loop,
            ctx.stop_ev,
            mode,
            ctx.planner_reply_queue,
            ctx.task_id,
            ctx.invocation_id,
        )
        try:
            while True:
                payload = await ctx.request_event_queue.get()
                elapsed_ms = int(time.time() * 1000) - start_time_ms
                out = {
                    **payload,
                    'elapsed_ms': elapsed_ms,
                    'stream_started_at': start_time_ms,
                    'invocation_id': payload.get('invocation_id') or ctx.invocation_id,
                }
                yield self.sse_format(out)
                if payload.get('type') == 'end':
                    break
            await future
        finally:
            self._queues.clear_planner_reply_queue(sid)


@lru_cache
def get_stream_service() -> ChatStreamService:
    return ChatStreamService(
        sessions_service=get_sessions_service(),
        events_service=get_events_service(),
        agent_run_service=get_agent_run_service(),
    )
