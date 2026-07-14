"""Subscription-only SSE flows for chat sessions and user wakeups."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from typing import Any

from src.dao.redis_dao import user_wakeup_channel
from src.services.run_interruption import (
    build_run_interrupted_history_content,
    build_run_interrupted_message,
    build_run_interrupted_meta,
)
from src.services.stream_queue_forwarder import (
    replay_history_and_follow_run_stream,
    start_subscription_before_history_replay,
)

logger = logging.getLogger(__name__)


class ChatStreamSubscriptionService:
    """Owns subscribe/replay and user wakeup streams, but never enqueues runs."""

    def __init__(
        self,
        service: Any,
        *,
        start_stream_subscription: Callable[..., Any],
        start_channel_subscription: Callable[..., Any],
        redis_enabled: bool,
        redis_dao_getter: Callable[[], Any],
        worker_registry_getter: Callable[[], Any],
        worker_id_getter: Callable[[], str],
    ) -> None:
        self._service = service
        self._sessions_service = service._sessions_service
        self._events_service = service._events_service
        self._deploy_state_service = service._deploy_state_service
        self._start_stream_subscription = start_stream_subscription
        self._start_channel_subscription = start_channel_subscription
        self._redis_enabled = redis_enabled
        self._redis_dao_getter = redis_dao_getter
        self._worker_registry_getter = worker_registry_getter
        self._worker_id_getter = worker_id_getter

    def sse_format(self, payload: dict) -> str:
        return self._service.sse_format(payload)

    def _ping_payload(self, session_id: str) -> dict:
        return self._service._ping_payload(session_id)

    def _iter_history_replay_batches(
        self, session_id: str, *, exclude_task_id: str | None = None
    ):
        return self._service._iter_history_replay_batches(
            session_id,
            exclude_task_id=exclude_task_id,
        )

    def _session_wakeup_payload(self, session_id: str, reason: str) -> dict:
        return self._service._session_wakeup_payload(session_id, reason)

    async def generate_subscribe_stream(self, session_id: str) -> AsyncGenerator[str]:
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
        status = payload.get("status")
        is_running_on_this_pod = self._sessions_service.is_session_running_on_this_pod(
            sid
        )
        is_run_on_another_pod = self._sessions_service.is_session_run_on_another_pod(
            sid
        )
        is_run_queued = bool(
            self._redis_enabled and self._redis_dao_getter().is_session_run_queued(sid)
        )
        is_stale = (
            status == "active"
            and not is_running_on_this_pod
            and not is_run_on_another_pod
            and not is_run_queued
        )
        run_owner = (
            self._worker_registry_getter().get_session_run_owner(sid)
            if status == "active"
            else None
        )
        owner_alive = (
            self._worker_registry_getter().is_worker_alive(run_owner)
            if run_owner
            else None
        )
        logger.info(
            "subscribe: session_id=%s status=%s is_running_on_this_pod=%s "
            "is_run_on_another_pod=%s is_run_queued=%s is_stale=%s run_owner=%s owner_alive=%s worker_id=%s",
            sid,
            status,
            is_running_on_this_pod,
            is_run_on_another_pod,
            is_run_queued,
            is_stale,
            run_owner,
            owner_alive,
            self._worker_id_getter(),
        )
        early_stream_subscription = None

        if is_stale:
            # 先区分原因再设状态：reason=restart 或 deploy 时会话状态设为 failed，否则设为 idle
            reason, reason_meta = self._deploy_state_service.classify_restart_reason(
                sid
            )
            if reason in ("restart", "deploy"):
                self._sessions_service.set_session_status(sid, "failed")
            else:
                self._sessions_service.reset_session_status_to_idle_in_db(sid)
            payload = self._sessions_service.get_session_status_payload(sid)
            last_query = self._events_service.get_last_user_query(sid)
            yield self.sse_format(payload)
            current_version = reason_meta.get("current_version")
            previous_version = reason_meta.get("previous_version")
            logger.info(
                "run_interrupted: stale session detected reason=%s "
                "session_id=%s prev=%s curr=%s",
                reason,
                sid,
                previous_version,
                current_version,
            )
            run_interrupted_content = build_run_interrupted_message(reason)
            last_user_content = (last_query or {}).get("content", "")
            # 共享的可选元数据字段，SSE payload 和入库内容都需要
            _meta = build_run_interrupted_meta(reason, reason_meta)
            run_interrupted_payload = {
                "source": "System",
                "type": "run_interrupted",
                "content": run_interrupted_content,
                "session_id": sid,
                "reason": reason,
                "last_user_content": last_user_content,
                **_meta,
            }
            yield self.sse_format(run_interrupted_payload)
            # 入库，便于历史/导出（如 CSV）中有重启记录；task_id 指向被中断的那一轮
            interrupted_task_id = payload.get("last_task_id")
            history_content = build_run_interrupted_history_content(
                reason=reason,
                reason_meta=reason_meta,
                last_user_content=last_user_content,
            )
            self._events_service.add_history_event(
                sid,
                {
                    "source": "System",
                    "type": "run_interrupted",
                    "content": history_content,
                    "session_id": sid,
                    "task_id": interrupted_task_id,
                },
                user_id=self._sessions_service.get_session_user_id(sid),
            )
            # reason=restart 或 deploy 时按失败处理：直接结束流并推送 stream_closed，不再等待
            if reason in ("restart", "deploy"):
                end_reason = (
                    "run_interrupted_restart"
                    if reason == "restart"
                    else "run_interrupted_deploy"
                )
                yield self.sse_format(
                    {
                        "source": "System",
                        "type": "stream_closed",
                        "content": run_interrupted_content,
                        "session_id": sid,
                        "end_reason": end_reason,
                        "treat_as_failure": True,
                    }
                )
                return
            # 不再自动重跑上次用户输入，由用户自行决定是否重新发送
        elif status == "waiting" and not is_run_queued:
            # DB 为 waiting 且 Redis 无 queued：若已有 run_owner 且存活则视为 active 不重置、继续流，否则重置为 idle 并结束流
            run_owner = self._worker_registry_getter().get_session_run_owner(sid)
            owner_alive = bool(
                run_owner and self._worker_registry_getter().is_worker_alive(run_owner)
            )
            if owner_alive:
                payload = {**payload, "status": "active"}
                yield self.sse_format(payload)
            else:
                self._sessions_service.reset_session_status_to_idle_in_db(sid)
                payload = self._sessions_service.get_session_status_payload(sid)
                yield self.sse_format(payload)
                return
        else:
            if self._redis_enabled and (is_run_on_another_pod or is_run_queued):
                early_stream_subscription = (
                    await start_subscription_before_history_replay(
                        sid,
                        start_stream_subscription=self._start_stream_subscription,
                        thread_name=f"stream-sub-{sid[:8]}",
                    )
                )
            yield self.sse_format(payload)
        async for chunk in replay_history_and_follow_run_stream(
            self,
            sid,
            start_stream_subscription=self._start_stream_subscription,
            initial_subscription=early_stream_subscription,
            is_run_on_another_pod=lambda: self._sessions_service.is_session_run_on_another_pod(
                sid
            ),
            is_run_queued=lambda: bool(
                self._redis_enabled
                and self._redis_dao_getter().is_session_run_queued(sid)
            ),
            redis_enabled=self._redis_enabled,
            thread_name=f"stream-sub-{sid[:8]}",
        ):
            yield chunk

    async def generate_wakeup_stream(self, user_id: str) -> AsyncGenerator[str]:
        """用户级 wakeup 流：订阅就绪后发送 snapshot，再转发 live wakeup。"""
        uid = (user_id or "").strip()

        def _snapshot_frames() -> list[str]:
            return [
                self.sse_format(
                    self._session_wakeup_payload(sid, "session_waiting_snapshot")
                )
                for sid in self._sessions_service.list_live_run_session_ids(uid)
            ]

        if not self._redis_enabled:
            for frame in _snapshot_frames():
                yield frame
            return

        loop = asyncio.get_running_loop()
        (
            redis_queue,
            shutdown_event,
            subscribe_ready,
            sub_thread,
        ) = self._start_channel_subscription(
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
