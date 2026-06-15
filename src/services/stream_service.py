"""Chat 流式接口业务逻辑：仅订阅流、发送消息流、交互回复 Redis 发布。"""

import asyncio
import json
import logging
import threading
import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache

from matmaster.config.exp import DEFAULT_MODE, SUPPORTED_MODES
from matmaster.context.sources.turn_input import TurnInput
from src.dao.redis_dao import (
    STREAM_CHANNEL_PREFIX,
    get_redis_dao,
    user_wakeup_channel,
)
from src.models.chat import ChatSendRequest, DeliverySpec
from src.services.chat_history import ChatHistoryConverter
from src.services.deploy_state_service import (
    DeployStateService,
    get_deploy_state_service,
)
from src.services.events_service import ChatEventsService, get_events_service
from src.services.session_directory_service import (
    SessionDirectoryResolver,
    normalize_remote_workspace_path,
)
from src.services.sessions_service import ChatSessionsService, get_sessions_service
from src.services.stream_queue_forwarder import (
    replay_history_and_follow_run_stream,
    start_subscription_before_history_replay,
    subscribe_enqueue_and_forward,
)
from src.services.stream_reply_queue import RedisReplyQueue
from src.services.stream_sse_filter import (
    REPLAY_DISCARDED_EVENT_TYPES,
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


def _start_redis_channel_subscription(
    channel: str,
    loop: asyncio.AbstractEventLoop,
    *,
    thread_name: str,
) -> tuple[asyncio.Queue, threading.Event, threading.Event, threading.Thread]:
    redis_queue: asyncio.Queue = asyncio.Queue()
    shutdown_event = threading.Event()
    subscribe_ready = threading.Event()

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
                if not msg:
                    continue
                msg_type = msg.get('type')
                if msg_type == 'subscribe':
                    subscribe_ready.set()
                    continue
                if msg_type != 'message':
                    continue
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
        name=thread_name,
        daemon=True,
    )
    sub_thread.start()
    return redis_queue, shutdown_event, subscribe_ready, sub_thread


def _start_redis_stream_subscription(
    session_id: str,
    loop: asyncio.AbstractEventLoop,
    *,
    thread_name: str,
) -> tuple[asyncio.Queue, threading.Event, threading.Event, threading.Thread]:
    return _start_redis_channel_subscription(
        STREAM_CHANNEL_PREFIX + session_id, loop, thread_name=thread_name
    )


@dataclass
class RunHandle:
    """_prepare_run 的成功产物：已写好发起事件、已组好 job，待 _enqueue_run 入队。"""

    task_id: str
    invocation_id: str
    job: dict
    event: dict  # 已落库的发起事件（User/query 或 System/trigger）


@dataclass
class Busy:
    """_prepare_run 因会话运行锁被占而放弃的产物。"""

    reason: str  # already_in_run | db_update_failed | unknown


@dataclass
class TriggerResult:
    """trigger_run 的返回。status: enqueued | deduped | busy | error。"""

    status: str
    task_id: str | None = None
    invocation_id: str | None = None
    dedup_key: str | None = None
    reason: str | None = None


@dataclass
class TriggerStreamContext:
    """内部 trigger 已写好发起事件、已组好 job，待订阅就绪后入队。"""

    task_id: str
    invocation_id: str
    owner: str
    job: dict
    event: dict  # 已落库的 System/trigger 发起事件
    dedup_key: str | None = None


@dataclass
class SendStreamContext:
    """发送消息流所需上下文，由 prepare_send_message 返回。"""

    task_id: str
    invocation_id: str  # 本轮调用的唯一标识，前端用于区分第几轮
    mode: str
    user_msg: dict
    job: dict  # _prepare_run 组好的入队 job；由 generate_send_stream 经 _enqueue_run 入队


class ChatStreamService:
    """流式接口服务：仅订阅流、发送消息流。"""

    def __init__(
        self,
        sessions_service: ChatSessionsService | None = None,
        events_service: ChatEventsService | None = None,
        deploy_state_service: DeployStateService | None = None,
    ) -> None:
        self._sessions_service = sessions_service or get_sessions_service()
        self._events_service = events_service or get_events_service()
        self._deploy_state_service = deploy_state_service or get_deploy_state_service()

    @staticmethod
    def sse_format(payload: dict) -> str:
        """ag-ui 协议：单条 SSE 格式为 event: ag-ui\\ndata: {json}\\n\\n"""
        return (
            f"event: {AG_UI_EVENT}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        )

    # 历史回放（事件已全部已知）时，把多条 SSE 帧合并成较大块再发，减少逐帧
    # yield / ASGI send / socket 写以及 gzip Z_SYNC_FLUSH 的次数。仅用于回放，
    # 不影响实时流的逐事件低延迟推送。多帧拼接仍是合法 SSE（前端按 \\n\\n 切分）。
    REPLAY_BATCH_MAX_BYTES = 64 * 1024

    def _iter_replayed_sse_batches(self, events):
        """过滤 + 规范化历史事件，并按 REPLAY_BATCH_MAX_BYTES 合并成 SSE 文本块逐块产出。"""
        buf: list[str] = []
        buf_len = 0
        for event in events:
            if not _should_emit_event_to_sse(event):
                continue
            frame = self.sse_format(_normalize_replayed_event(event))
            buf.append(frame)
            buf_len += len(frame)
            if buf_len >= self.REPLAY_BATCH_MAX_BYTES:
                yield ''.join(buf)
                buf = []
                buf_len = 0
        if buf:
            yield ''.join(buf)

    def _iter_history_replay_batches(
        self, session_id: str, *, exclude_task_id: str | None = None
    ):
        events = (
            self._events_service.get_session_events(
                session_id,
                include_spawn=True,
                exclude_types=REPLAY_DISCARDED_EVENT_TYPES,
            )
            or []
        )
        if exclude_task_id:
            events = ChatHistoryConverter.exclude_task_events(events, exclude_task_id)
        events = _normalize_replayed_compaction_events(events)
        events = _dedupe_replayed_terminal_events(events)
        yield from self._iter_replayed_sse_batches(_inject_elapsed_for_history(events))

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
                "failed to snapshot pre_turn_history_event_id session_id=%s",
                session_id,
                exc_info=True,
            )
            return None
        return value if isinstance(value, int) else None

    @staticmethod
    def _resolve_mode(mode: str | None) -> str:
        """归一化 chat mode：空白或未知一律回落到 DEFAULT_MODE。"""
        resolved = (mode or DEFAULT_MODE).strip().lower() or DEFAULT_MODE
        return resolved if resolved in SUPPORTED_MODES else DEFAULT_MODE

    def _prepare_run(
        self,
        session_id: str,
        *,
        user_id: str,
        user_text: str,
        files: list[str] | None,
        images: list[str] | None,
        workspace_paths: list[str] | None,
        event_writer: Callable[[str, str], dict],
        id_prefix: str,
        mode: str,
        model: str | None = None,
        byok_credential_id: str | None = None,
        bohrium_required: bool = False,
        workspace: str | None = None,
        origin: str | None = None,
        delivery: dict | None = None,
        pre_event_hook: Callable[[], None] | None = None,
    ) -> RunHandle | Busy:
        """共享内核：确保会话、占锁、快照边界、写发起事件并组装 job。

        不负责 lpush，以保护用户发送路径 subscribe-before-enqueue 的不变量。
        占锁失败时直接返回 Busy，由调用方决定后续策略。
        """
        sid = session_id.strip()
        workspace_value = (
            normalize_remote_workspace_path(workspace) if workspace else None
        )
        self._sessions_service.ensure_session(sid, user_id=user_id)
        acquired_ok, reason = self._sessions_service.try_acquire_session_run(sid)
        if not acquired_ok:
            return Busy(reason=reason or "unknown")
        if pre_event_hook is not None:
            pre_event_hook()
        task_id = id_prefix + uuid.uuid4().hex[:16]
        invocation_id = 'inv_' + uuid.uuid4().hex[:16]
        self._sessions_service.set_session_last_task(sid, task_id, user_id=user_id)
        self._deploy_state_service.record_session_version(sid)
        pre_turn_history_event_id = self._get_pre_turn_history_event_id(sid) or 0
        turn_input = TurnInput.from_values(
            user_text=user_text,
            files=files,
            images=images,
            workspace_paths=workspace_paths,
            pre_turn_history_event_id=pre_turn_history_event_id,
        )
        event = event_writer(task_id, invocation_id)
        job = {
            'session_id': sid,
            'task_id': task_id,
            'invocation_id': invocation_id,
            'user_prompt': turn_input.user_text,
            'mode': mode,
            'model': model,
            'byok_credential_id': byok_credential_id,
            'turn_input': turn_input.to_payload(),
            'images': list(images or []),
            # 纯用户/会话意图；workspace ⇒ 必须上 Bohrium 的推导统一在 run_bohrium_stage
            'bohrium_required': bool(bohrium_required),
            'workspace': workspace_value,
            'origin': origin,
            'delivery': delivery,
            'submitted_at': datetime.now(timezone.utc).isoformat(),
        }
        return RunHandle(
            task_id=task_id,
            invocation_id=invocation_id,
            job=job,
            event=event,
        )

    def _notify_run_queued(self, session_id: str, job: dict) -> None:
        """发送任务进入排队运维通知，失败不影响入队。"""
        sid = session_id.strip()
        try:
            session_user_id = self._sessions_service.get_session_user_id(sid)
            user_info = UserService.get_user_info_for_display(session_user_id)
            user_info_display = f"{user_info['user_id']} | {user_info['nickname']} | {user_info['email']}"
            env = (SERVICE_ENV or '').strip().lower()
            session_url = f"https://matmaster{'' if not env or env == 'prod' else f'.{env}'}.bohrium.com/matmaster/chat-evo/{sid}"
            queue_len = get_redis_dao().llen_agent_run_queue()
            active_count = get_worker_registry_service().count_active_runs()
            user_question = (job.get('user_prompt') or '').strip()
            if len(user_question) > 500:
                user_question = user_question[:500] + '…'
            notify_post_async(
                '任务进入排队',
                [
                    ('会话ID', sid),
                    ('会话地址', session_url),
                    ('用户', user_info_display),
                    ('模型', format_llm_model_for_notify(job.get('model'))),
                    ('用户问题', user_question or '-'),
                    ('排队数', str(queue_len)),
                    ('执行中', str(active_count)),
                ],
                template=CARD_TEMPLATE_ORANGE,
            )
        except Exception as e:
            logger.warning('Feishu 进入排队通知发送失败 session_id=%s: %s', sid, e)

    def _enqueue_run(self, session_id: str, job: dict) -> bool:
        """共享入队：set waiting、标记 queued、脱离本 pod 占用、通知、lpush。"""
        sid = session_id.strip()
        self._sessions_service.set_session_status(sid, 'waiting')
        get_redis_dao().set_session_run_queued(sid)
        self._sessions_service.discard_session_run_from_this_pod(sid)
        self._notify_run_queued(sid, job)
        if not get_redis_dao().lpush_agent_run_job(job):
            self._sessions_service.set_session_status(sid, 'idle')
            get_redis_dao().delete_session_run_queued(sid)
            return False
        return True

    @staticmethod
    def _session_wakeup_payload(session_id: str, reason: str) -> dict:
        """session 唤醒事件 payload；snapshot 与 live publish 共用同一 schema。"""
        return {
            "source": "System",
            "type": "session_wakeup",
            "reason": reason,
            "session_id": session_id.strip(),
        }

    def _publish_user_wakeup(self, user_id: str, session_id: str, reason: str) -> None:
        """向用户级 wakeup channel 发布一条 session 唤醒信号。"""
        payload = self._session_wakeup_payload(session_id, reason)
        if not get_redis_dao().publish_user_wakeup(user_id, payload):
            logger.warning(
                "publish_user_wakeup failed user_id=%s session_id=%s reason=%s",
                user_id,
                session_id,
                reason,
            )

    def _finalize_enqueue(self, ctx: TriggerStreamContext, session_id: str) -> bool:
        """提交内部 trigger：入队成功后标记 dedup 并发布 wakeup；失败返回 False。"""
        if not self._enqueue_run(session_id, ctx.job):
            return False
        if ctx.dedup_key:
            get_redis_dao().mark_dedup_key_nx(ctx.dedup_key, ctx.task_id)
        self._publish_user_wakeup(ctx.owner, session_id, "trigger_enqueued")
        return True

    def prepare_internal_trigger_run(
        self,
        session_id: str,
        prompt: str,
        *,
        origin: str,
        dedup_key: str | None = None,
        delivery: DeliverySpec | None = None,
        mode: str | None = None,
        model: str | None = None,
        workspace: str | None = None,
    ) -> TriggerResult | TriggerStreamContext:
        """准备内部 trigger：校验 owner/dedup，写 System/trigger，组装 job，不入队。"""
        sid = session_id.strip()
        owner = self._sessions_service.get_session_user_id(sid)
        if not owner:
            logger.warning(
                "trigger prepare rejected: session not found or no owner session_id=%s",
                sid,
            )
            return TriggerResult(status="error", reason="session_not_found_or_no_owner")

        if dedup_key and get_redis_dao().dedup_key_exists(dedup_key):
            logger.info(
                "trigger prepare deduped session_id=%s dedup_key=%s", sid, dedup_key
            )
            return TriggerResult(status="deduped", dedup_key=dedup_key)

        resolved_mode = self._resolve_mode(mode)
        model_val = (model or '').strip() or None
        if model_val is None:
            model_val = self._events_service.get_last_resolved_model_profile(sid)
        delivery_payload = delivery.model_dump() if delivery is not None else None

        def _system_event_writer(task_id: str, invocation_id: str) -> dict:
            event = {
                'source': 'System',
                'type': 'trigger',
                'content': {'text': prompt, 'origin': origin},
                'session_id': sid,
                'task_id': task_id,
                'invocation_id': invocation_id,
            }
            self._events_service.add_history_event(sid, event, user_id=owner)
            return event

        handle = self._prepare_run(
            sid,
            user_id=owner,
            user_text=prompt,
            files=None,
            images=None,
            workspace_paths=None,
            event_writer=_system_event_writer,
            id_prefix='trig_',
            mode=resolved_mode,
            model=model_val,
            byok_credential_id=None,
            workspace=workspace,
            origin=origin,
            delivery=delivery_payload,
        )
        if isinstance(handle, Busy):
            logger.info(
                "trigger prepare busy session_id=%s reason=%s", sid, handle.reason
            )
            return TriggerResult(status="busy", reason=handle.reason)

        return TriggerStreamContext(
            task_id=handle.task_id,
            invocation_id=handle.invocation_id,
            owner=owner,
            job=handle.job,
            event=handle.event,
            dedup_key=dedup_key,
        )

    def trigger_run(
        self,
        session_id: str,
        prompt: str,
        *,
        origin: str,
        dedup_key: str | None = None,
        delivery: DeliverySpec | None = None,
        mode: str | None = None,
        model: str | None = None,
        workspace: str | None = None,
    ) -> TriggerResult:
        """程序化触发一次 agent run。"""
        sid = session_id.strip()
        prep = self.prepare_internal_trigger_run(
            sid,
            prompt,
            origin=origin,
            dedup_key=dedup_key,
            delivery=delivery,
            mode=mode,
            model=model,
            workspace=workspace,
        )
        if isinstance(prep, TriggerResult):
            return prep

        if not self._finalize_enqueue(prep, sid):
            return TriggerResult(status="error", reason="enqueue_failed")
        logger.info(
            "trigger_run enqueued session_id=%s task_id=%s origin=%s",
            sid,
            prep.task_id,
            origin,
        )
        return TriggerResult(
            status="enqueued",
            task_id=prep.task_id,
            invocation_id=prep.invocation_id,
        )

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
        payload = self._sessions_service.get_session_status_payload(sid)
        # 部署/重启后：DB 仍为 active 但本进程没有该 session 的 run → 视为上一轮在别的 pod 上被中断
        # 若 Redis 显示该 session 的 run 在别的 worker 上，则是「切会话后落到另一实例」，不是重启，不当作 stale
        # 若任务已入队但 Worker 尚未接手（worker 满等情况），run_owner 可能仍为 API 进程且不刷新 worker_alive，此时也不应视为 stale
        status = payload.get('status')
        is_running_on_this_pod = self._sessions_service.is_session_running_on_this_pod(
            sid
        )
        is_run_on_another_pod = self._sessions_service.is_session_run_on_another_pod(
            sid
        )
        is_run_queued = bool(REDIS_URL and get_redis_dao().is_session_run_queued(sid))
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
        early_stream_subscription = None

        if is_stale:
            # 先区分原因再设状态：reason=restart 或 deploy 时会话状态设为 failed，否则设为 idle
            reason, reason_meta = self._deploy_state_service.classify_restart_reason(
                sid
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
                run_owner and get_worker_registry_service().is_worker_alive(run_owner)
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
            if REDIS_URL and (is_run_on_another_pod or is_run_queued):
                early_stream_subscription = (
                    await start_subscription_before_history_replay(
                        sid,
                        start_stream_subscription=_start_redis_stream_subscription,
                        thread_name=f"stream-sub-{sid[:8]}",
                    )
                )
            yield self.sse_format(payload)
        async for chunk in replay_history_and_follow_run_stream(
            self,
            sid,
            start_stream_subscription=_start_redis_stream_subscription,
            initial_subscription=early_stream_subscription,
            is_run_on_another_pod=lambda: self._sessions_service.is_session_run_on_another_pod(
                sid
            ),
            is_run_queued=lambda: bool(
                REDIS_URL and get_redis_dao().is_session_run_queued(sid)
            ),
            redis_enabled=bool(REDIS_URL),
            thread_name=f"stream-sub-{sid[:8]}",
        ):
            yield chunk

    async def generate_wakeup_stream(self, user_id: str) -> AsyncGenerator[str, None]:
        """用户级 wakeup 流：订阅就绪后发送 snapshot，再转发 live wakeup。"""
        uid = (user_id or "").strip()

        def _snapshot_frames() -> list[str]:
            return [
                self.sse_format(
                    self._session_wakeup_payload(sid, "session_waiting_snapshot")
                )
                for sid in self._sessions_service.list_waiting_or_active_session_ids(
                    uid
                )
            ]

        if not REDIS_URL:
            for frame in _snapshot_frames():
                yield frame
            return

        loop = asyncio.get_running_loop()
        (
            redis_queue,
            shutdown_event,
            subscribe_ready,
            sub_thread,
        ) = _start_redis_channel_subscription(
            user_wakeup_channel(uid),
            loop,
            thread_name=f"wakeup-{uid[:8]}",
        )
        try:
            if not await asyncio.to_thread(subscribe_ready.wait, 3.0):
                logger.warning(
                    "generate_wakeup_stream: redis subscribe not ready before "
                    "snapshot user_id=%s",
                    uid,
                )
            for frame in _snapshot_frames():
                yield frame
            while True:
                try:
                    payload = await asyncio.wait_for(redis_queue.get(), timeout=30.0)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield self.sse_format(payload)
        finally:
            shutdown_event.set()
            sub_thread.join(timeout=2.0)

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

        mode = self._resolve_mode(req.mode)

        model = (
            req.model or ''
        ).strip() or None  # 本轮模型名，如 matmaster/qwen3.7-max / claude-sonnet-4-6
        byok_credential_id = (req.byok_credential_id or '').strip() or None

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

        user_content = (req.content or '').strip()

        def _run_pre_event_hook() -> None:
            if req.replace_last_turn:
                last_query_ev = self._events_service.get_last_user_query_event(sid)
                if last_query_ev and last_query_ev.get('id'):
                    self._events_service.delete_events_from_id(sid, last_query_ev['id'])
                    logger.info(
                        "replace_last_turn: deleted events from id=%s session_id=%s",
                        last_query_ev['id'],
                        sid,
                    )
            if req.bohrium_project_id is not None or org_id is not None:
                self._sessions_service.set_session_bohrium(
                    sid,
                    org_id=org_id_val,
                    project_id=project_id_val,
                )
            if user_content and user_id:
                try:
                    self._sessions_service.set_session_chat_mode(sid, mode, user_id)
                except Exception as e:
                    logger.warning(
                        "persist chat_mode failed (best-effort) session_id=%s: %s",
                        sid,
                        e,
                    )

        def _user_event_writer(task_id: str, invocation_id: str) -> dict:
            user_msg = {
                'source': 'User',
                'type': 'query',
                'content': user_content,
                'mode': mode,
                'session_id': sid,
                'task_id': task_id,
                'invocation_id': invocation_id,
            }
            if model:
                user_msg['requested_model'] = model
            if req.files:
                user_msg['files'] = list(req.files)
            if req.images:
                user_msg['images'] = list(req.images)
            if req.workspace_paths:
                user_msg['workspace_paths'] = list(req.workspace_paths)
            if resolved_directory.source != "none":
                user_msg["session_directory"] = resolved_directory.remote_workdir
                user_msg["session_directory_source"] = resolved_directory.source
            self._events_service.add_history_event(sid, user_msg, user_id=user_id)
            return user_msg

        handle = self._prepare_run(
            sid,
            user_id=user_id,
            user_text=user_content,
            files=req.files,
            images=req.images,
            workspace_paths=req.workspace_paths,
            event_writer=_user_event_writer,
            id_prefix='sse_',
            mode=mode,
            model=model,
            byok_credential_id=byok_credential_id,
            bohrium_required=bohrium_required,
            workspace=resolved_directory.remote_workdir,
            origin=None,
            delivery=None,
            pre_event_hook=_run_pre_event_hook,
        )
        if isinstance(handle, Busy):
            return None

        dao = get_redis_dao()
        dao.delete_interaction_reply_list(sid)
        return SendStreamContext(
            task_id=handle.task_id,
            invocation_id=handle.invocation_id,
            mode=mode,
            user_msg=handle.event,
            job=handle.job,
        )

    def get_reply_queue(self, session_id: str) -> RedisReplyQueue | None:
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
        """Publish a user interaction reply to Redis stream."""
        sid = session_id.strip()
        if REDIS_URL:
            get_redis_dao().publish_stream_event(sid, payload)

    async def generate_send_stream(
        self,
        session_id: str,
        ctx: SendStreamContext,
    ) -> AsyncGenerator[str, None]:
        """
        发送消息流：先推送历史 + 用户消息 + 状态，再订阅 Redis channel 推送 Worker 事件。
        """
        sid = session_id.strip()
        logger.info(
            'generate_send_stream: start session_id=%s task_id=%s mode=%s',
            sid,
            ctx.task_id,
            ctx.mode,
        )
        async for chunk in subscribe_enqueue_and_forward(
            self,
            sid,
            initiating_event=ctx.user_msg,
            task_id=ctx.task_id,
            invocation_id=ctx.invocation_id,
            thread_name=f"send-stream-queue-{sid[:8]}",
            enqueue=lambda: self._enqueue_run(sid, ctx.job),
            start_stream_subscription=_start_redis_stream_subscription,
        ):
            yield chunk

    async def generate_internal_trigger_stream(
        self,
        session_id: str,
        ctx: TriggerStreamContext,
    ) -> AsyncGenerator[str, None]:
        """内部 HTTP trigger 流：订阅就绪后才入队，再转发 Worker 实时事件。"""
        sid = session_id.strip()
        logger.info(
            'generate_internal_trigger_stream: start session_id=%s task_id=%s',
            sid,
            ctx.task_id,
        )
        async for chunk in subscribe_enqueue_and_forward(
            self,
            sid,
            initiating_event=ctx.event,
            task_id=ctx.task_id,
            invocation_id=ctx.invocation_id,
            thread_name=f"trigger-stream-{sid[:8]}",
            enqueue=lambda: self._finalize_enqueue(ctx, sid),
            start_stream_subscription=_start_redis_stream_subscription,
        ):
            yield chunk


@lru_cache
def get_stream_service() -> ChatStreamService:
    return ChatStreamService(
        sessions_service=get_sessions_service(),
        events_service=get_events_service(),
        deploy_state_service=get_deploy_state_service(),
    )
