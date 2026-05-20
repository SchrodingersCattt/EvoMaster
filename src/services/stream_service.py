"""Chat 流式接口业务逻辑：SSE 队列管理、仅订阅流、发送消息流。
交互回复队列：无 Redis 用进程内 queue；有 Redis 用 redis_dao 的 run_active + reply list，多 worker 可写。"""

import asyncio
import json
import logging
import threading
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Protocol, runtime_checkable

from matmaster.config.exp import DEFAULT_MODE, SUPPORTED_MODES
from matmaster.context.sources.turn_input import TurnInput
from src.dao.redis_dao import (
    STREAM_CHANNEL_PREFIX,
    get_redis_dao,
)
from src.models.chat import ChatSendRequest
from src.services.agent_run_service import (
    AgentRunService,
    get_agent_run_service,
)
from src.services.chat_history import ChatHistoryConverter
from src.services.deploy_state_service import (
    DeployStateService,
    get_deploy_state_service,
)
from src.services.events_service import ChatEventsService, get_events_service
from src.services.session_directory_service import (
    SessionDirectoryResolver,
    SessionDirectorySource,
)
from src.services.sessions_service import ChatSessionsService, get_sessions_service
from src.services.stream_reply_queue import RedisReplyQueue
from src.services.stream_sse_filter import (
    _dedupe_replayed_terminal_events,
    _inject_elapsed_for_history,
    _normalize_replayed_compaction_events,
    _normalize_replayed_event,
    _should_emit_event_to_sse,
)
from src.services.user_service import UserService
from src.services.worker_registry_service import get_worker_registry_service
from src.utils.constant import AG_UI_EVENT, REDIS_URL, SERVICE_ENV
from src.utils.feishu_notifier import (
    CARD_TEMPLATE_ORANGE,
    format_llm_model_for_notify,
    notify_post_async,
)
from src.utils.worker_id import get_worker_id

logger = logging.getLogger(__name__)


@runtime_checkable
class ReplyQueueLike(Protocol):
    """Interaction reply queue abstraction used by stream/chat endpoints."""

    def put_content(self, content: str) -> None: ...

    def put_cancel(self) -> None: ...

    def get(self, timeout: float | None = None) -> str | None: ...


class StreamQueueManager:
    """流式接口的队列管理：SSE 订阅队列的注册/注销与广播；当前 run 的交互回复队列（ask_question 共用）。"""

    def __init__(self) -> None:
        # session_id -> 该会话下所有 SSE 连接的队列，agent 事件会广播到这些队列
        self._sse_queues: dict[str, list[asyncio.Queue]] = {}

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
    llm: str | None = None  # 本轮使用的 LLM 配置块名，不传则用 agent 默认
    model: str | None = None  # 本轮使用的模型名（覆盖 LLM 配置里的 model）
    turn_input: TurnInput | None = None
    bohrium_required: bool = False  # 本轮是否显式依赖 Bohrium access_key / project
    images: list[str] = field(default_factory=list)
    remote_workdir: str | None = None
    session_directory_source: SessionDirectorySource = "none"


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
    def _ping_payload(session_id: str) -> dict:
        return {
            'source': 'System',
            'type': 'ping',
            'content': '',
            'session_id': session_id,
        }

    @staticmethod
    def _build_run_interrupted_message(reason: str) -> str:
        if reason == 'restart':
            return '上一轮任务因服务重启中断，请重新发送以继续。'
        if reason == 'deploy':
            return '上一轮任务因服务升级中断，请重新发送以继续。'
        return '上一轮任务因服务部署/重启中断，请重新发送以继续。'

    def _get_pre_turn_history_event_id(self, session_id: str) -> int | None:
        try:
            value = self._events_service.get_latest_scope_event_id(session_id, None)
        except Exception:
            logger.warning(
                "failed to snapshot pre-query scope event id session_id=%s",
                session_id,
                exc_info=True,
            )
            return None
        return value if isinstance(value, int) else None

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
                run_interrupted_content = self._build_run_interrupted_message(reason)
                last_user_content = (last_query or {}).get('content', '')
                # 共享的可选元数据字段，SSE payload 和入库内容都需要
                _meta: dict = {}
                if current_version:
                    _meta['current_version'] = current_version
                if previous_version:
                    _meta['previous_version'] = previous_version
                if reason_meta.get('note'):
                    _meta['reason_note'] = reason_meta['note']
                if reason in ('restart', 'deploy'):
                    _meta['treat_as_failure'] = True
                run_interrupted_payload = {
                    'source': 'System',
                    'type': 'run_interrupted',
                    'content': run_interrupted_content,
                    'session_id': sid,
                    'reason': reason,
                    'last_user_content': last_user_content,
                    **_meta,
                }
                yield self.sse_format(run_interrupted_payload)
                # 入库，便于历史/导出（如 CSV）中有重启记录；task_id 指向被中断的那一轮
                interrupted_task_id = payload.get('last_task_id')
                history_content = {
                    'message': run_interrupted_content,
                    'reason': reason,
                    'last_user_content': last_user_content,
                    **_meta,
                }
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
                # reason=restart 或 deploy 时按失败处理：直接结束流并推送 stream_closed，不再等待
                if reason in ('restart', 'deploy'):
                    end_reason = (
                        'run_interrupted_restart'
                        if reason == 'restart'
                        else 'run_interrupted_deploy'
                    )
                    yield self.sse_format(
                        {
                            'source': 'System',
                            'type': 'stream_closed',
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
            events = self._events_service.get_session_events(sid, include_spawn=True)
            if events:
                events = _normalize_replayed_compaction_events(events)
                events = _dedupe_replayed_terminal_events(events)
                events = _inject_elapsed_for_history(events)
                for event in events:
                    if _should_emit_event_to_sse(event):
                        yield self.sse_format(_normalize_replayed_event(event))

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
                        shutdown_event = threading.Event()
                        loop = asyncio.get_event_loop()
                        channel = STREAM_CHANNEL_PREFIX + sid

                        def _redis_subscribe_loop(
                            _channel: str = channel,
                            _shutdown_ev: threading.Event = shutdown_event,
                            _ev_loop: asyncio.AbstractEventLoop = loop,
                            _queue: asyncio.Queue = redis_queue,
                        ) -> None:
                            client = get_redis_dao().create_client()
                            if not client:
                                return
                            pubsub = client.pubsub()
                            try:
                                pubsub.subscribe(_channel)
                                while not _shutdown_ev.is_set():
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
                                except TimeoutError:
                                    yield self.sse_format(self._ping_payload(sid))
                                    continue
                                if payload.get('type') in {'stream_closed', 'end'}:
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
                            shutdown_event.set()
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
                        yield self.sse_format(self._ping_payload(sid))
                else:
                    # 仅「已入队未接手」：ping 保活，等待 Worker 接手或 queued 超时
                    await asyncio.sleep(5.0)
                    yield self.sse_format(self._ping_payload(sid))
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
        req_fields = req.model_dump(exclude_unset=True)
        self._sessions_service.ensure_session(sid, user_id=user_id)

        resolved_directory = SessionDirectoryResolver(self._sessions_service).resolve(
            session_id=sid,
            request_directory=req.directory,
            request_directory_provided="directory" in req_fields,
        )

        acquired_ok, _ = self._sessions_service.try_acquire_session_run(sid)
        if not acquired_ok:
            return None

        if req.replace_last_turn:
            last_query_ev = self._events_service.get_last_user_query_event(sid)
            if last_query_ev and last_query_ev.get('id'):
                self._events_service.delete_events_from_id(sid, last_query_ev['id'])
                logger.info(
                    "replace_last_turn: deleted events from id=%s session_id=%s",
                    last_query_ev['id'],
                    sid,
                )

        mode = (req.mode or DEFAULT_MODE).strip().lower() or DEFAULT_MODE
        if mode not in SUPPORTED_MODES:
            mode = DEFAULT_MODE

        llm = (req.llm or '').strip() or None
        model = (
            req.model or ''
        ).strip() or None  # 本轮模型名，如 gemini-3-flash-preview / claude-sonnet-4-6

        org_id_val = org_id.strip() if org_id else None
        try:
            project_id_val = (
                int(req.bohrium_project_id)
                if req.bohrium_project_id is not None
                else None
            )
        except (TypeError, ValueError):
            project_id_val = None
        bohrium_required = bool(
            (org_id_val and project_id_val is not None)
            or resolved_directory.bohrium_required
        )

        # Bohrium：org_id / project_id 直接入库，需要时从库读，不常驻内存
        if req.bohrium_project_id is not None or org_id is not None:
            self._sessions_service.set_session_bohrium(
                sid,
                org_id=org_id_val,
                project_id=project_id_val,
            )

        task_id = 'sse_' + uuid.uuid4().hex[:16]
        invocation_id = 'inv_' + uuid.uuid4().hex[:16]
        self._sessions_service.set_session_last_task(sid, task_id, user_id=user_id)
        self._deploy_state_service.record_session_version(sid)
        user_content = (req.content or '').strip()
        if user_content and user_id:
            self._sessions_service.set_session_chat_mode(sid, mode, user_id)
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
        if req.images:
            user_msg['images'] = list(req.images)
        if req.workspace_paths:
            user_msg['workspace_paths'] = list(req.workspace_paths)
        if resolved_directory.source != "none":
            user_msg["session_directory"] = resolved_directory.remote_workdir
            user_msg["session_directory_source"] = resolved_directory.source
        pre_turn_history_event_id = self._get_pre_turn_history_event_id(sid) or 0
        turn_input = TurnInput.from_values(
            user_text=user_content,
            files=req.files,
            images=req.images,
            workspace_paths=req.workspace_paths,
            pre_turn_history_event_id=pre_turn_history_event_id,
        )
        self._events_service.add_history_event(sid, user_msg, user_id=user_id)

        dao = get_redis_dao()
        dao.delete_interaction_reply_list(sid)
        request_event_queue: asyncio.Queue = asyncio.Queue()

        return SendStreamContext(
            task_id=task_id,
            invocation_id=invocation_id,
            mode=mode,
            user_msg=user_msg,
            request_event_queue=request_event_queue,
            llm=llm,
            model=model,
            turn_input=turn_input,
            bohrium_required=bohrium_required,
            images=list(req.images or []),
            remote_workdir=resolved_directory.remote_workdir,
            session_directory_source=resolved_directory.source,
        )

    def get_reply_queue(self, session_id: str) -> ReplyQueueLike | None:
        """供 POST /ask_question_reply 写入使用；无活跃 run 时返回 None。仅 Worker 队列模式，由 Redis run_active 判定。"""
        if not REDIS_URL:
            return None
        if get_redis_dao().is_interaction_run_active(session_id):
            return RedisReplyQueue(session_id)
        return None

    def get_run_context(self, session_id: str) -> dict | None:
        """当前 run 的 task_id / invocation_id。仅 Worker 队列模式，从 Redis 取。供写入历史等用。"""
        if not REDIS_URL:
            return None
        return get_redis_dao().get_interaction_run_context(session_id)

    def publish_reply_event(self, session_id: str, payload: dict) -> None:
        """Publish a user interaction reply to local subscribers and Redis stream."""
        sid = session_id.strip()
        self._queues.broadcast(sid, payload)
        if REDIS_URL:
            get_redis_dao().publish_stream_event(sid, payload)

    def _send_cb(
        self,
        session_id: str,
        request_event_queue: asyncio.Queue,
        payload: dict,
    ) -> None:
        """供 run_agent 的 send_cb 使用：写入本连接队列并广播到订阅队列；有 Redis 时同时发布到 stream channel 供其它 pod 的 subscribe 流消费。"""
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
        history = self._events_service.get_session_events(sid, include_spawn=True) or []
        history = ChatHistoryConverter.exclude_task_events(history, ctx.task_id)
        history = _normalize_replayed_compaction_events(history)
        history = _dedupe_replayed_terminal_events(history)
        history = _inject_elapsed_for_history(history)
        for event in history:
            if _should_emit_event_to_sse(event):
                yield self.sse_format(_normalize_replayed_event(event))
        yield self.sse_format(ctx.user_msg)

        def send_cb(payload: dict):
            """同步回调：用 call_soon_threadsafe 把事件投递到 event loop，不等待。
            避免因 SSE 写阻塞导致 run_coroutine_threadsafe 超时、事件已落库但未推送到前端。"""
            loop.call_soon_threadsafe(
                self._send_cb, sid, ctx.request_event_queue, payload
            )

        redis_queue = asyncio.Queue()
        shutdown_event = threading.Event()
        subscribe_ready = threading.Event()
        channel = STREAM_CHANNEL_PREFIX + sid

        def _redis_subscribe_loop() -> None:
            client = get_redis_dao().create_client()
            if not client:
                subscribe_ready.set()
                return
            pubsub = client.pubsub()
            try:
                pubsub.subscribe(channel)
                while not shutdown_event.is_set():
                    msg = pubsub.get_message(timeout=1.0)
                    if msg and msg.get('type') == 'subscribe':
                        subscribe_ready.set()
                        continue
                    if msg and msg.get('type') == 'message':
                        try:
                            data = json.loads(msg['data'])
                            loop.call_soon_threadsafe(redis_queue.put_nowait, data)
                        except (json.JSONDecodeError, TypeError):
                            pass
            finally:
                subscribe_ready.set()
                try:
                    pubsub.unsubscribe(channel)
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
            if not await asyncio.to_thread(subscribe_ready.wait, 3.0):
                logger.warning(
                    'generate_send_stream: redis subscribe not ready before enqueue session_id=%s task_id=%s',
                    sid,
                    ctx.task_id,
                )
            turn_input_payload = (
                ctx.turn_input.to_payload() if ctx.turn_input is not None else None
            )
            legacy_current_input_payload = None
            if ctx.turn_input is not None and turn_input_payload is not None:
                legacy_current_input_payload = {
                    **turn_input_payload,
                    "pre_query"
                    + "_scope_event_id": (ctx.turn_input.pre_turn_history_event_id),
                }

            job = {
                'session_id': sid,
                'task_id': ctx.task_id,
                'invocation_id': ctx.invocation_id,
                'user_prompt': user_prompt,
                'mode': mode,
                'llm': ctx.llm,
                'model': ctx.model,
                'turn_input': turn_input_payload,
                'current_input_context': legacy_current_input_payload,
                'images': list(ctx.images),
                'bohrium_required': ctx.bohrium_required,
                'remote_workdir': ctx.remote_workdir,
                'session_directory_source': ctx.session_directory_source,
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
                env = (SERVICE_ENV or '').strip().lower()
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
                        'type': 'stream_closed',
                        'content': '',
                        'session_id': sid,
                        'invocation_id': ctx.invocation_id,
                    }
                )
                return
            try:
                while True:
                    try:
                        payload = await asyncio.wait_for(
                            redis_queue.get(), timeout=30.0
                        )
                    except TimeoutError:
                        yield self.sse_format(self._ping_payload(sid))
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
                    if payload.get('type') in {'stream_closed', 'end'}:
                        break
            finally:
                shutdown_event.set()
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
