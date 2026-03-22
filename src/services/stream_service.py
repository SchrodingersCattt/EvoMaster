"""Chat 流式接口业务逻辑：SSE 队列管理、仅订阅流、发送消息流。
确认回复队列：无 Redis 用进程内 queue；有 Redis 用 redis_dao 的 run_active + reply list，多 worker 可写。"""

import asyncio
import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import AsyncGenerator, Callable

from src.dao.redis_dao import (
    CONFIRMATION_CANCEL_VALUE,
    STREAM_CHANNEL_PREFIX,
    get_redis_dao,
)
from src.models.chat import ChatSendRequest
from src.services.agent_run_service import (
    AgentRunService,
    ReplyQueueLike,
    get_agent_run_service,
)
from src.services.deploy_state_service import (
    DeployStateService,
    get_deploy_state_service,
)
from src.services.events_service import ChatEventsService, get_events_service
from src.services.sessions_service import ChatSessionsService, get_sessions_service
from src.services.user_service import UserService
from src.services.worker_registry_service import get_worker_registry_service
from src.utils.constant import AG_UI_EVENT, CURRENT_ENV, REDIS_URL
from src.utils.feishu_notifier import (
    CARD_TEMPLATE_ORANGE,
    format_llm_model_for_notify,
    notify_post_async,
)
from src.utils.worker_id import get_worker_id

logger = logging.getLogger(__name__)

# 进程内队列用的取消哨兵（get 时转为 None）
_CANCEL_SENTINEL = object()


def _should_emit_event_to_sse(event: dict) -> bool:
    """Persisted event types that must not be sent to the client SSE (see playground.mat_master.core.run_helpers.should_skip_push_for_frontend)."""
    t = event.get('type')
    if t == 'log_line':
        return False
    if t == 'assistant_state':
        return False
    return True


class InMemoryReplyQueue:
    """进程内队列封装，实现 ReplyQueueLike。"""

    def __init__(self, q: queue.Queue) -> None:
        self._q = q

    def put_content(self, content: str) -> None:
        self._q.put(content)

    def put_cancel(self) -> None:
        self._q.put(_CANCEL_SENTINEL)

    def get(self, timeout: float | None = None) -> str | None:
        try:
            v = self._q.get(timeout=timeout)
        except queue.Empty:
            raise
        if v is _CANCEL_SENTINEL:
            return None
        return v


class RedisReplyQueue:
    """基于 Redis List 的回复队列，任意 worker 可 put_content/put_cancel，执行 run 的 worker 可 get。"""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id.strip()
        self._dao = get_redis_dao()

    def put_content(self, content: str) -> None:
        self._dao.rpush_confirmation_reply(self._session_id, content)

    def put_cancel(self) -> None:
        self._dao.rpush_confirmation_reply(self._session_id, CONFIRMATION_CANCEL_VALUE)

    def get(self, timeout: float | None = None) -> str | None:
        # timeout=None 表示 BLOCK 模式，Redis BLPOP timeout=0 表示一直阻塞
        sec = 0 if timeout is None else int(timeout) if timeout >= 0 else 300
        value = self._dao.blpop_confirmation_reply(self._session_id, sec)
        if value is None:
            raise queue.Empty
        if value == CONFIRMATION_CANCEL_VALUE:
            return None
        return value


class ReplyQueueNotifyOnGet:
    """包装 ReplyQueueLike：在 get() 返回用户回复时调用 on_reply(content)。
    用于在「执行 agent 的 worker」上注入 confirmation_reply，保证多 worker 下顺序正确（POST 可能打到其他 worker）。
    """

    def __init__(self, inner: ReplyQueueLike, on_reply: Callable[[str], None]) -> None:
        self._inner = inner
        self._on_reply = on_reply

    def put_content(self, content: str) -> None:
        self._inner.put_content(content)

    def put_cancel(self) -> None:
        self._inner.put_cancel()

    def get(self, timeout: float | None = None) -> str | None:
        result = self._inner.get(timeout=timeout)
        if result is not None and self._on_reply:
            logger.info(
                'ReplyQueueNotifyOnGet.get: got user reply len=%s, scheduling inject',
                len(result),
            )
            self._on_reply(result)
        return result


class StreamQueueManager:
    """流式接口的队列管理：SSE 订阅队列的注册/注销与广播；当前 run 的确认回复队列（confirmation_request 共用）。"""

    def __init__(self) -> None:
        # session_id -> 该会话下所有 SSE 连接的队列，agent 事件会广播到这些队列
        self._sse_queues: dict[str, list[asyncio.Queue]] = {}
        # session_id -> 当前 run 的确认回复队列（仅无 Redis 时使用；有 Redis 时由 get_reply_queue 按 run_active 返回 RedisReplyQueue）
        self._reply_queues: dict[str, ReplyQueueLike] = {}
        # session_id -> 当前 run 的 request_event_queue（供 broadcast_reply 时注入 confirmation_reply，保证发送流内事件顺序）
        self._request_event_queues: dict[str, tuple[asyncio.Queue, str, str]] = {}

    def set_reply_queue(self, session_id: str, q: ReplyQueueLike) -> None:
        """注册该会话当前 run 的确认回复队列（仅 in-memory 路径调用）。"""
        self._reply_queues[session_id.strip()] = q

    def get_reply_queue(self, session_id: str) -> ReplyQueueLike | None:
        """供 POST /confirmation_reply 写入使用；无活跃 run 时返回 None。"""
        return self._reply_queues.get(session_id.strip())

    def set_request_event_queue(
        self,
        session_id: str,
        queue: asyncio.Queue,
        task_id: str,
        invocation_id: str,
    ) -> None:
        """注册当前 run 的 request_event_queue，便于 confirmation_reply 按序注入发送流。"""
        self._request_event_queues[session_id.strip()] = (queue, task_id, invocation_id)

    def get_request_event_queue(
        self, session_id: str
    ) -> tuple[asyncio.Queue, str, str] | None:
        """返回 (queue, task_id, invocation_id)，无则 None。"""
        return self._request_event_queues.get(session_id.strip())

    def clear_reply_queue(self, session_id: str) -> None:
        """run 结束后从内存表移除；有 Redis 时由 ChatStreamService.clear_reply_queue 负责 Redis 清理。"""
        sid = session_id.strip()
        self._reply_queues.pop(sid, None)
        self._request_event_queues.pop(sid, None)

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
    reply_queue: (
        ReplyQueueLike  # confirmation_request 共用，POST /confirmation_reply 写入
    )
    llm: str | None = None  # 本轮使用的 LLM 配置块名，不传则用 agent 默认
    model: str | None = None  # 本轮使用的模型名（覆盖 LLM 配置里的 model）


class ChatStreamService:
    """流式接口服务：仅订阅流、发送消息流。队列由 StreamQueueManager 管理。"""

    def __init__(
        self,
        queue_manager: StreamQueueManager | None = None,
        sessions_service: ChatSessionsService | None = None,
        events_service: ChatEventsService | None = None,
        agent_run_service: AgentRunService | None = None,
        deploy_state_service: DeployStateService | None = None,
    ) -> None:
        self._queues = queue_manager or StreamQueueManager()
        self._sessions_service = sessions_service or get_sessions_service()
        self._events_service = events_service or get_events_service()
        self._agent_run_service = agent_run_service or get_agent_run_service()
        self._deploy_state_service = deploy_state_service or get_deploy_state_service()

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

    @staticmethod
    def _build_run_interrupted_message(
        reason: str, previous_version: str | None, current_version: str | None
    ) -> str:
        if reason == 'restart':
            return '上一轮任务因服务重启中断，请重新发送以继续。'
        if previous_version and current_version:
            return (
                '上一轮任务因服务升级'
                f'（{previous_version} -> {current_version}）中断，请重新发送以继续。'
            )
        if current_version:
            return f'上一轮任务因服务升级到 {current_version} 中断，请重新发送以继续。'
        return '上一轮任务因服务部署/重启中断，请重新发送以继续。'

    async def generate_subscribe_stream(
        self, session_id: str
    ) -> AsyncGenerator[str, None]:
        """
        仅订阅模式：先推送当前会话状态与历史事件，再注册到订阅队列。
        流会保持打开直到该 session 的 run 结束（仅 Worker 队列模式，run 在 Worker 上）：
        - 若 run 在 Worker（其它 pod）：有 Redis 时订阅 chat:stream:{session_id} 收实时事件并推送，收到 end 或 run 结束后结束流；
          无 Redis 时轮询直到 run 结束（每 5s 发 ping 保活），再推送 session_status(idle) 后结束流。
        - 若仅「已入队未接手」：ping 保活直到 Worker 接手或 queued 超时。
        若 DB 为 active 但 Worker 上也无该 run（部署/重启导致上一 run 已死），
        则重置为 idle、推送 run_interrupted（原因：部署）；不自动重跑，由用户决定是否重新发送。
        """
        sid = session_id.strip()
        event_queue = self._queues.register_subscriber(sid)
        try:
            payload = self._sessions_service.get_session_status_payload(sid)
            # 部署/重启后：DB 仍为 active 但本进程没有该 session 的 run → 视为上一轮在别的 pod 上被中断
            # 若 Redis 显示该 session 的 run 在别的 worker 上，则是「切会话后落到另一实例」，不是重启，不当作 stale
            # 若任务已入队但 Worker 尚未接手（worker 满等情况），run_owner 可能仍为 API 进程且不刷新 worker_alive，此时也不应视为 stale
            status = payload.get('status')
            is_running_on_this_pod = (
                self._sessions_service.is_session_running_on_this_pod(sid)
            )
            is_run_on_another_pod = (
                self._sessions_service.is_session_run_on_another_pod(sid)
            )
            is_run_queued = bool(
                REDIS_URL and get_redis_dao().is_session_run_queued(sid)
            )
            is_stale = (
                status == 'active'
                and not is_running_on_this_pod
                and not is_run_on_another_pod
                and not is_run_queued
            )
            run_owner = (
                get_worker_registry_service().get_session_run_owner(sid)
                if status == 'active'
                else None
            )
            owner_alive = (
                get_worker_registry_service().is_worker_alive(run_owner)
                if run_owner
                else None
            )
            logger.info(
                'subscribe: session_id=%s status=%s is_running_on_this_pod=%s '
                'is_run_on_another_pod=%s is_run_queued=%s is_stale=%s run_owner=%s owner_alive=%s worker_id=%s',
                sid,
                status,
                is_running_on_this_pod,
                is_run_on_another_pod,
                is_run_queued,
                is_stale,
                run_owner,
                owner_alive,
                get_worker_id(),
            )
            if is_stale:
                # 先区分原因再设状态：reason=restart 或 deploy 时会话状态设为 failed，否则设为 idle
                reason, reason_meta = (
                    self._deploy_state_service.classify_restart_reason(sid)
                )
                if reason in ('restart', 'deploy'):
                    self._sessions_service.set_session_status(sid, 'failed')
                else:
                    self._sessions_service.reset_session_status_to_idle_in_db(sid)
                payload = self._sessions_service.get_session_status_payload(sid)
                last_query = self._events_service.get_last_user_query(sid)
                yield self.sse_format(payload)
                current_version = reason_meta.get('current_version')
                previous_version = reason_meta.get('previous_version')
                logger.info(
                    'run_interrupted: stale session detected reason=%s '
                    'session_id=%s prev=%s curr=%s',
                    reason,
                    sid,
                    previous_version,
                    current_version,
                )
                run_interrupted_content = self._build_run_interrupted_message(
                    reason, previous_version, current_version
                )
                last_user_content = (last_query or {}).get('content', '')
                run_interrupted_payload = {
                    'source': 'System',
                    'type': 'run_interrupted',
                    'content': run_interrupted_content,
                    'session_id': sid,
                    'reason': reason,
                    'last_user_content': last_user_content,
                }
                if current_version:
                    run_interrupted_payload['current_version'] = current_version
                if previous_version:
                    run_interrupted_payload['previous_version'] = previous_version
                if reason_meta.get('note'):
                    run_interrupted_payload['reason_note'] = reason_meta['note']
                # reason=restart 或 deploy 时按失败处理：便于前端直接结束流并展示为失败
                if reason in ('restart', 'deploy'):
                    run_interrupted_payload['treat_as_failure'] = True
                yield self.sse_format(run_interrupted_payload)
                # 入库，便于历史/导出（如 CSV）中有重启记录；task_id 指向被中断的那一轮
                interrupted_task_id = payload.get('last_task_id')
                history_content = {
                    'message': run_interrupted_content,
                    'reason': reason,
                    'last_user_content': last_user_content,
                }
                if current_version:
                    history_content['current_version'] = current_version
                if previous_version:
                    history_content['previous_version'] = previous_version
                if reason_meta.get('note'):
                    history_content['reason_note'] = reason_meta['note']
                if reason in ('restart', 'deploy'):
                    history_content['treat_as_failure'] = True
                self._events_service.add_history_event(
                    sid,
                    {
                        'source': 'System',
                        'type': 'run_interrupted',
                        'content': history_content,
                        'session_id': sid,
                        'task_id': interrupted_task_id,
                    },
                    user_id=self._sessions_service.get_session_user_id(sid),
                )
                # reason=restart 或 deploy 时按失败处理：直接结束流并推送 end，不再等待
                if reason in ('restart', 'deploy'):
                    end_reason = (
                        'run_interrupted_restart'
                        if reason == 'restart'
                        else 'run_interrupted_deploy'
                    )
                    yield self.sse_format(
                        {
                            'source': 'System',
                            'type': 'end',
                            'content': run_interrupted_content,
                            'session_id': sid,
                            'end_reason': end_reason,
                            'treat_as_failure': True,
                        }
                    )
                    return
                # 不再自动重跑上次用户输入，由用户自行决定是否重新发送
            elif status == 'waiting' and not is_run_queued:
                # DB 为 waiting 且 Redis 无 queued：若已有 run_owner 且存活则视为 active 不重置、继续流，否则重置为 idle 并结束流
                run_owner = get_worker_registry_service().get_session_run_owner(sid)
                owner_alive = bool(
                    run_owner
                    and get_worker_registry_service().is_worker_alive(run_owner)
                )
                if owner_alive:
                    payload = {**payload, 'status': 'active'}
                    yield self.sse_format(payload)
                else:
                    self._sessions_service.reset_session_status_to_idle_in_db(sid)
                    payload = self._sessions_service.get_session_status_payload(sid)
                    yield self.sse_format(payload)
                    return
            else:
                yield self.sse_format(payload)
            events = self._events_service.get_session_events(sid)
            if events:
                events = self._inject_elapsed_for_history(events)
                for event in events:
                    if _should_emit_event_to_sse(event):
                        yield self.sse_format(event)

            # 保持流打开直到 Worker 上的 run 结束，或「已入队未接手」结束；仅队列模式，run 不在 API 进程
            def _run_still_active() -> bool:
                if self._sessions_service.is_session_run_on_another_pod(sid):
                    return True
                if REDIS_URL and get_redis_dao().is_session_run_queued(sid):
                    return True
                return False

            while _run_still_active():
                if self._sessions_service.is_session_run_on_another_pod(sid):
                    # run 在别的 pod：有 Redis 时订阅 stream channel 收实时事件，否则轮询 + ping 保活
                    if REDIS_URL:
                        redis_queue = asyncio.Queue()
                        stop_event = threading.Event()
                        loop = asyncio.get_event_loop()
                        channel = STREAM_CHANNEL_PREFIX + sid

                        def _redis_subscribe_loop(
                            _channel: str = channel,
                            _stop_ev: threading.Event = stop_event,
                            _ev_loop: asyncio.AbstractEventLoop = loop,
                            _queue: asyncio.Queue = redis_queue,
                        ) -> None:
                            client = get_redis_dao().create_client()
                            if not client:
                                return
                            pubsub = client.pubsub()
                            try:
                                pubsub.subscribe(_channel)
                                while not _stop_ev.is_set():
                                    msg = pubsub.get_message(timeout=1.0)
                                    if msg and msg.get('type') == 'message':
                                        try:
                                            data = json.loads(msg['data'])
                                            _ev_loop.call_soon_threadsafe(
                                                _queue.put_nowait, data
                                            )
                                        except (json.JSONDecodeError, TypeError):
                                            pass
                            finally:
                                try:
                                    pubsub.unsubscribe(_channel)
                                    pubsub.close()
                                except Exception:
                                    pass

                        sub_thread = threading.Thread(
                            target=_redis_subscribe_loop,
                            name=f'stream-sub-{sid[:8]}',
                            daemon=True,
                        )
                        sub_thread.start()
                        try:
                            while self._sessions_service.is_session_run_on_another_pod(
                                sid
                            ):
                                try:
                                    payload = await asyncio.wait_for(
                                        redis_queue.get(), timeout=30.0
                                    )
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
                                if payload.get('type') == 'end':
                                    yield self.sse_format(payload)
                                    break
                                yield self.sse_format(payload)
                            else:
                                payload = (
                                    self._sessions_service.get_session_status_payload(
                                        sid
                                    )
                                )
                                yield self.sse_format(payload)
                        finally:
                            stop_event.set()
                            sub_thread.join(timeout=2.0)
                    else:
                        await asyncio.sleep(5.0)
                        if not self._sessions_service.is_session_run_on_another_pod(
                            sid
                        ):
                            payload = self._sessions_service.get_session_status_payload(
                                sid
                            )
                            yield self.sse_format(payload)
                            break
                        yield self.sse_format(
                            {
                                'source': 'System',
                                'type': 'ping',
                                'content': '',
                                'session_id': sid,
                            }
                        )
                else:
                    # 仅「已入队未接手」：ping 保活，等待 Worker 接手或 queued 超时
                    await asyncio.sleep(5.0)
                    yield self.sse_format(
                        {
                            'source': 'System',
                            'type': 'ping',
                            'content': '',
                            'session_id': sid,
                        }
                    )
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
        为发送消息做准备：确保会话、尝试占用 run、更新 Bohrium 凭证、写入用户消息、创建队列。
        若该会话已有任务在运行则返回 None（调用方应返回 409）。
        """
        sid = session_id.strip()
        # 仅 Worker 队列模式：无 Redis 时无法发送
        if not REDIS_URL:
            return None
        self._sessions_service.ensure_session(sid, user_id=user_id)
        acquired_ok, _ = self._sessions_service.try_acquire_session_run(sid)
        if not acquired_ok:
            return None

        mode = (req.mode or 'direct').strip().lower() or 'direct'
        if mode not in ('direct', 'planner'):
            mode = 'direct'

        llm = (req.llm or '').strip() or None
        model = (
            req.model or ''
        ).strip() or None  # 本轮模型名，如 gemini-3-flash-preview / azure/gpt-5

        # Bohrium：org_id / project_id 直接入库，需要时从库读，不常驻内存
        if req.bohrium_project_id is not None or org_id is not None:
            try:
                project_id_val = (
                    int(req.bohrium_project_id)
                    if req.bohrium_project_id is not None
                    else None
                )
            except (TypeError, ValueError):
                project_id_val = None
            self._sessions_service.set_session_bohrium(
                sid,
                org_id=org_id.strip() if org_id else None,
                project_id=project_id_val,
            )

        task_id = 'sse_' + uuid.uuid4().hex[:16]
        invocation_id = 'inv_' + uuid.uuid4().hex[:16]
        self._sessions_service.set_session_last_task(sid, task_id, user_id=user_id)
        self._deploy_state_service.record_session_version(sid)
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
        if req.workspace_paths:
            user_msg['workspace_paths'] = list(req.workspace_paths)
        self._events_service.add_history_event(sid, user_msg, user_id=user_id)

        dao = get_redis_dao()
        dao.delete_confirmation_reply_list(sid)
        reply_queue: ReplyQueueLike = RedisReplyQueue(sid)
        request_event_queue: asyncio.Queue = asyncio.Queue()

        return SendStreamContext(
            task_id=task_id,
            invocation_id=invocation_id,
            mode=mode,
            user_msg=user_msg,
            request_event_queue=request_event_queue,
            reply_queue=reply_queue,
            llm=llm,
            model=model,
        )

    def get_reply_queue(self, session_id: str) -> ReplyQueueLike | None:
        """供 POST /confirmation_reply 写入使用；无活跃 run 时返回 None。仅 Worker 队列模式，由 Redis run_active 判定。"""
        if not REDIS_URL:
            return None
        if get_redis_dao().is_confirmation_run_active(session_id):
            return RedisReplyQueue(session_id)
        return None

    def get_run_context(self, session_id: str) -> dict | None:
        """当前 run 的 task_id / invocation_id。仅 Worker 队列模式，从 Redis 取。供写入历史等用。"""
        if not REDIS_URL:
            return None
        return get_redis_dao().get_confirmation_run_context(session_id)

    def broadcast_reply(self, session_id: str, content: str) -> None:
        """将用户确认回复广播到该会话所有 SSE 订阅（confirmation_request 统一用 confirmation_reply）。
        发送流内的 confirmation_reply 由 ReplyQueueNotifyOnGet 在 agent 的 get() 返回时注入，保证顺序且多 worker 下也正确。
        broadcast 的 payload 带上 task_id/invocation_id（同 worker 从内存取，多 worker 从 Redis 取），便于前端去重或排序。
        """
        sid = session_id.strip()
        payload = {
            'source': 'User',
            'type': 'confirmation_reply',
            'content': content,
            'session_id': sid,
        }
        if REDIS_URL:
            ctx = get_redis_dao().get_confirmation_run_context(session_id)
            if ctx:
                payload['task_id'] = ctx.get('task_id')
                payload['invocation_id'] = ctx.get('invocation_id')
        self._queues.broadcast(session_id, payload)

    def _send_cb(
        self,
        session_id: str,
        request_event_queue: asyncio.Queue,
        payload: dict,
    ) -> None:
        """供 run_agent_sync 的 send_cb 使用：写入本连接队列并广播到订阅队列；有 Redis 时同时发布到 stream channel 供其它 pod 的 subscribe 流消费。"""
        request_event_queue.put_nowait(payload)
        self._queues.broadcast(session_id, payload)
        if REDIS_URL:
            get_redis_dao().publish_stream_event(session_id, payload)

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
        loop = asyncio.get_running_loop()
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
            if _should_emit_event_to_sse(event):
                yield self.sse_format(event)
        yield self.sse_format(ctx.user_msg)

        def send_cb(payload: dict):
            """同步回调：用 call_soon_threadsafe 把事件投递到 event loop，不等待。
            避免因 SSE 写阻塞导致 run_coroutine_threadsafe 超时、事件已落库但未推送到前端。"""
            loop.call_soon_threadsafe(
                self._send_cb, sid, ctx.request_event_queue, payload
            )

        def _inject_confirmation_reply(content: str) -> None:
            """在 event loop 中执行：将 confirmation_reply 注入本连接 request_event_queue，保证顺序在 tool_result 前（多 worker 下也成立）。"""
            payload = {
                'source': 'User',
                'type': 'confirmation_reply',
                'content': content,
                'session_id': sid,
                'task_id': ctx.task_id,
                'invocation_id': ctx.invocation_id,
            }
            try:
                ctx.request_event_queue.put_nowait(payload)
                logger.info(
                    'confirmation_reply injected into request_event_queue session_id=%s task_id=%s',
                    sid,
                    ctx.task_id,
                )
            except Exception as e:
                logger.warning(
                    'confirmation_reply inject failed session_id=%s: %s',
                    sid,
                    e,
                )

        def _on_reply(content: str) -> None:
            loop.call_soon_threadsafe(_inject_confirmation_reply, content)

        ReplyQueueNotifyOnGet(ctx.reply_queue, _on_reply)

        try:
            job = {
                'session_id': sid,
                'task_id': ctx.task_id,
                'invocation_id': ctx.invocation_id,
                'user_prompt': user_prompt,
                'mode': mode,
                'llm': ctx.llm,
                'model': ctx.model,
                'submitted_at': datetime.now(timezone.utc).isoformat(),
            }
            # 先设为 waiting 再入队，避免 Worker 接手后 set active 被此处覆盖（竞态）
            self._sessions_service.set_session_status(sid, 'waiting')
            get_redis_dao().set_session_run_queued(sid)
            self._sessions_service.discard_session_run_from_this_pod(sid)
            # 在入队之前发「任务进入排队」飞书通知，避免 Worker 先拿到任务先发「开始执行」导致顺序颠倒
            try:
                session_user_id = self._sessions_service.get_session_user_id(sid)
                user_info = UserService.get_user_info_for_display(session_user_id)
                user_info_display = f"{user_info['user_id']} | {user_info['nickname']} | {user_info['email']}"
                env = (CURRENT_ENV or '').strip().lower()
                session_url = f"https://matmaster{'' if not env or env == 'prod' else f'.{env}'}.bohrium.com/matmaster/chat-evo/{sid}"
                queue_len = get_redis_dao().llen_agent_run_queue()
                active_count = get_worker_registry_service().count_active_runs()
                user_question = (user_prompt or '').strip()
                if len(user_question) > 500:
                    user_question = user_question[:500] + '…'
                notify_post_async(
                    '任务进入排队',
                    [
                        ('会话ID', sid),
                        ('会话地址', session_url),
                        ('用户', user_info_display),
                        ('模型', format_llm_model_for_notify(ctx.llm, ctx.model)),
                        ('用户问题', user_question or '-'),
                        ('排队数', str(queue_len)),
                        ('执行中', str(active_count)),
                    ],
                    template=CARD_TEMPLATE_ORANGE,
                )
            except Exception as e:
                logger.warning('Feishu 进入排队通知发送失败 session_id=%s: %s', sid, e)
            if not get_redis_dao().lpush_agent_run_job(job):
                self._sessions_service.set_session_status(sid, 'idle')
                get_redis_dao().delete_session_run_queued(sid)
                yield self.sse_format(
                    {
                        'source': 'System',
                        'type': 'error',
                        'content': 'Queue unavailable.',
                        'session_id': sid,
                        'invocation_id': ctx.invocation_id,
                    }
                )
                yield self.sse_format(
                    {
                        'source': 'System',
                        'type': 'end',
                        'content': '',
                        'session_id': sid,
                        'invocation_id': ctx.invocation_id,
                    }
                )
                return
            redis_queue = asyncio.Queue()
            stop_event = threading.Event()
            channel = STREAM_CHANNEL_PREFIX + sid

            def _redis_subscribe_loop(
                _channel: str = channel,
                _stop_ev: threading.Event = stop_event,
                _ev_loop: asyncio.AbstractEventLoop = loop,
                _queue: asyncio.Queue = redis_queue,
            ) -> None:
                client = get_redis_dao().create_client()
                if not client:
                    return
                pubsub = client.pubsub()
                try:
                    pubsub.subscribe(_channel)
                    while not _stop_ev.is_set():
                        msg = pubsub.get_message(timeout=1.0)
                        if msg and msg.get('type') == 'message':
                            try:
                                data = json.loads(msg['data'])
                                _ev_loop.call_soon_threadsafe(_queue.put_nowait, data)
                            except (json.JSONDecodeError, TypeError):
                                pass
                finally:
                    try:
                        pubsub.unsubscribe(_channel)
                        pubsub.close()
                    except Exception:
                        pass

            sub_thread = threading.Thread(
                target=_redis_subscribe_loop,
                name=f'send-stream-queue-{sid[:8]}',
                daemon=True,
            )
            sub_thread.start()
            try:
                while True:
                    try:
                        payload = await asyncio.wait_for(
                            redis_queue.get(), timeout=30.0
                        )
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
                    elapsed_ms = int(time.time() * 1000) - start_time_ms
                    out = {
                        **payload,
                        'elapsed_ms': elapsed_ms,
                        'stream_started_at': start_time_ms,
                        'invocation_id': payload.get('invocation_id')
                        or ctx.invocation_id,
                    }
                    yield self.sse_format(out)
                    if payload.get('type') == 'end':
                        break
            finally:
                stop_event.set()
                sub_thread.join(timeout=2.0)
        finally:
            # 不断开即不 put_cancel：仅用户显式点「停止」(POST /stop) 才取消，刷新/关 Tab 后可在新页继续回复
            pass


@lru_cache
def get_stream_service() -> ChatStreamService:
    return ChatStreamService(
        sessions_service=get_sessions_service(),
        events_service=get_events_service(),
        agent_run_service=get_agent_run_service(),
        deploy_state_service=get_deploy_state_service(),
    )
