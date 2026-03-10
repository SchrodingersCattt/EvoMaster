"""Agent Worker 入口：从 Redis 队列 BLPOP 任务，执行 run_agent_sync；事件由 run_agent_sync 内 event_callback 写 DB，本处仅 publish 到 Redis。
供独立 Worker Deployment 使用，与 API 共用同一代码库与镜像（Dockerfile --target worker）。
Worker 需周期刷新 worker_alive，否则 API 在用户刷新页面时会误判 run 为 stale 并推送 run_interrupted。
"""

import logging
import os
import signal
import sys
import threading
import time

from src.dao.redis_dao import get_redis_dao
from src.services.agent_run_service import get_agent_run_service
from src.services.sessions_service import get_sessions_service
from src.services.stream_service import RedisReplyQueue
from src.services.worker_registry_service import get_worker_registry_service
from src.utils.build_info import get_build_version
from src.utils.logger import LoggingConfig, setup_logging
from src.utils.worker_id import get_worker_id

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# BLPOP 超时（秒），超时后继续循环，便于进程能响应 SIGTERM
_BLPOP_TIMEOUT = int(os.environ.get('AGENT_WORKER_BLPOP_TIMEOUT', '30'))
# 存活心跳间隔（秒），需小于 Redis WORKER_ALIVE_TTL_SEC(30)，否则 API 会误判本进程已死
_WORKER_HEARTBEAT_INTERVAL = 10.0
# 当前正在跑的 session_id（由主循环设置/清除），供心跳线程刷新 run_owner TTL，避免长任务超过 SESSION_RUN_OWNER_TTL 后 API 误判 stale
_current_session_id: str | None = None
# 优雅退出：SIGTERM 时设为 True，主循环在「当前 run 结束后」或「空闲时」退出，不再接新任务
_drain_requested = False


class RedisBackedStopEvent:
    """供 Worker 使用：is_set() 从 Redis 读取用户是否请求停止。"""

    def __init__(self, session_id: str, task_id: str):
        self._session_id = session_id
        self._task_id = task_id
        self._dao = get_redis_dao()

    def is_set(self) -> bool:
        return self._dao.is_stop_requested(self._session_id, self._task_id)


def _publish_run_interrupted_deploy(session_id: str) -> None:
    """SIGTERM 时若当前有 run，向该 session 的 stream 推送 run_interrupted(deploy) + end，前端可立即得知因部署中断。"""
    sid = session_id.strip()
    if not sid:
        return
    redis_dao = get_redis_dao()
    if not redis_dao.get_publish_client():
        return
    previous_version = get_build_version()
    content = (
        f'上一轮任务因服务升级（{previous_version} -> 新版本）中断，请重新发送以继续。'
        if previous_version
        else '上一轮任务因服务部署中断，请重新发送以继续。'
    )
    run_interrupted_payload = {
        'source': 'System',
        'type': 'run_interrupted',
        'content': content,
        'session_id': sid,
        'reason': 'deploy',
        'treat_as_failure': True,
    }
    if previous_version:
        run_interrupted_payload['previous_version'] = previous_version
    run_interrupted_payload['reason_note'] = 'worker_sigterm'
    try:
        redis_dao.publish_stream_event(sid, run_interrupted_payload)
        redis_dao.publish_stream_event(
            sid,
            {
                'source': 'System',
                'type': 'end',
                'content': content,
                'session_id': sid,
                'end_reason': 'run_interrupted_deploy',
                'treat_as_failure': True,
            },
        )
        logger.info(
            'Agent worker: published run_interrupted(deploy)+end for session_id=%s worker_id=%s',
            sid,
            get_worker_id(),
        )
    except Exception as e:
        logger.warning(
            'Agent worker: publish run_interrupted failed session_id=%s: %s',
            sid,
            e,
        )


def _worker_heartbeat_loop(stop_ev: threading.Event) -> None:
    """后台线程：周期刷新本进程 worker_alive 与当前 session 的 run_owner TTL，使 API subscribe 时 is_worker_alive(owner) 为 True，
    且长任务不会因 SESSION_RUN_OWNER_TTL（2h）过期导致 run_owner 丢失、误判 stale 并推送 run_interrupted。"""
    while not stop_ev.wait(timeout=_WORKER_HEARTBEAT_INTERVAL):
        try:
            get_worker_registry_service().set_worker_alive(get_worker_id())
            # 刷新当前 run 的 session_run_owner TTL，避免超过 2h 后 key 过期、API 侧 run_owner=None 触发 run_interrupted
            sid = _current_session_id
            if sid:
                get_worker_registry_service().refresh_session_run_owner(
                    sid, get_worker_id()
                )
        except Exception as e:
            logger.warning(
                'Agent worker heartbeat skipped worker_id=%s: %s', get_worker_id(), e
            )


def _run_worker_loop() -> None:
    global _current_session_id
    redis_dao = get_redis_dao()
    if not redis_dao.create_client():
        logger.error(
            'Agent worker: REDIS_URL not configured or Redis unreachable. Exit.'
        )
        sys.exit(1)

    sessions_service = get_sessions_service()
    agent_run_service = get_agent_run_service()

    agent_run_service.init_playground_sync()

    while True:
        payload = redis_dao.blpop_agent_run_job(timeout_sec=_BLPOP_TIMEOUT)
        if payload is None:
            if _drain_requested:
                logger.info(
                    'Agent worker: drain requested, no current job, exiting loop. worker_id=%s',
                    get_worker_id(),
                )
                return
            continue

        session_id = (payload.get('session_id') or '').strip()
        task_id = payload.get('task_id') or ''
        invocation_id = payload.get('invocation_id')
        user_prompt = payload.get('user_prompt') or ''
        mode = (payload.get('mode') or 'direct').strip().lower() or 'direct'
        llm_override = (payload.get('llm') or '').strip() or None
        model_override = (payload.get('model') or '').strip() or None

        if not session_id:
            logger.warning('Agent worker: skip job with empty session_id')
            continue

        redis_dao.delete_confirmation_reply_list(session_id)
        redis_dao.set_confirmation_run_active(session_id)
        redis_dao.set_confirmation_run_context(session_id, task_id, invocation_id or '')

        def send_cb(p: dict, _sid: str = session_id) -> None:
            # 不在此处写 DB：run_agent_sync 内 event_callback 已写，此处再写会导致同一条事件落库两次
            redis_dao.publish_stream_event(_sid, p)

        reply_queue: RedisReplyQueue = RedisReplyQueue(session_id)
        stop_ev = RedisBackedStopEvent(session_id, task_id)
        acquired = False

        try:
            acquired_ok, fail_reason = sessions_service.try_acquire_session_run(
                session_id
            )
            if not acquired_ok and fail_reason == 'db_update_failed':
                logger.info(
                    'Agent worker: db_update_failed, retry once after 2s session_id=%s task_id=%s',
                    session_id,
                    task_id,
                )
                time.sleep(2)
                acquired_ok, fail_reason = sessions_service.try_acquire_session_run(
                    session_id
                )
            if not acquired_ok:
                logger.warning(
                    'Agent worker: skip job session_id=%s task_id=%s reason=%s',
                    session_id,
                    task_id,
                    fail_reason or 'unknown',
                )
                redis_dao.delete_confirmation_run_active(session_id)
                continue

            acquired = True
            _current_session_id = session_id
            run_success = True
            try:
                result = agent_run_service.run_agent_sync(
                    session_id=session_id,
                    user_prompt=user_prompt,
                    send_cb=send_cb,
                    loop=None,
                    stop_event=stop_ev,
                    mode=mode,
                    reply_queue=reply_queue,
                    task_id=task_id,
                    invocation_id=invocation_id,
                    llm_override=llm_override,
                    model_override=model_override,
                )
                run_success = result is not False
            except Exception as e:
                run_success = False
                logger.exception(
                    'Agent worker: run_agent_sync failed session_id=%s task_id=%s: %s',
                    session_id,
                    task_id,
                    e,
                )
                try:
                    send_cb(
                        {
                            'source': 'System',
                            'type': 'error',
                            'content': str(e),
                            'session_id': session_id,
                            'task_id': task_id,
                            'invocation_id': invocation_id,
                        }
                    )
                    send_cb(
                        {
                            'source': 'System',
                            'type': 'end',
                            'content': '',
                            'session_id': session_id,
                            'task_id': task_id,
                            'invocation_id': invocation_id,
                        }
                    )
                except Exception:
                    pass
        finally:
            if acquired:
                _current_session_id = None
            redis_dao.delete_confirmation_run_active(session_id)
            redis_dao.delete_stop_requested(session_id, task_id)
            if acquired:
                sessions_service.release_session_run(
                    session_id, run_success=run_success
                )
        if _drain_requested:
            logger.info(
                'Agent worker: drain requested, current job finished, exiting loop. session_id=%s worker_id=%s',
                session_id,
                get_worker_id(),
            )
            return


def main() -> None:
    setup_logging(**LoggingConfig.get_worker_config())

    def _on_sigterm(_signum: int, _frame: object) -> None:
        global _drain_requested
        sid = _current_session_id
        if sid:
            _publish_run_interrupted_deploy(sid)
        _drain_requested = True
        logger.info(
            'Agent worker: received SIGTERM, drain requested; will exit after current job or when idle. worker_id=%s',
            get_worker_id(),
        )

    signal.signal(signal.SIGTERM, _on_sigterm)

    # 心跳线程：使 API 能通过 is_worker_alive(owner) 识别本进程仍在跑，刷新页面时不误判 run_interrupted
    _heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_worker_heartbeat_loop,
        args=(_heartbeat_stop,),
        name='agent_worker_heartbeat',
        daemon=True,
    )
    heartbeat_thread.start()
    logger.info(
        'Agent worker: heartbeat thread started interval=%.0fs worker_id=%s',
        _WORKER_HEARTBEAT_INTERVAL,
        get_worker_id(),
    )

    logger.info(
        'Agent worker: starting BLPOP loop queue_key=%s', 'chat:agent_run_queue'
    )
    _run_worker_loop()


if __name__ == '__main__':
    main()
