"""Agent Worker 入口：从 Redis 队列 BLPOP 任务，执行 run_agent_sync；事件由 run_agent_sync 内 event_callback 写 DB，本处仅 publish 到 Redis。
供独立 Worker Deployment 使用，与 API 共用同一代码库与镜像（Dockerfile --target worker）。
"""

import logging
import os
import signal
import sys

from src.dao.redis_dao import get_redis_dao
from src.services.agent_run_service import get_agent_run_service
from src.services.sessions_service import get_sessions_service
from src.services.stream_service import RedisReplyQueue

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# BLPOP 超时（秒），超时后继续循环，便于进程能响应 SIGTERM
_BLPOP_TIMEOUT = int(os.environ.get('AGENT_WORKER_BLPOP_TIMEOUT', '30'))


class RedisBackedStopEvent:
    """供 Worker 使用：is_set() 从 Redis 读取用户是否请求停止。"""

    def __init__(self, session_id: str, task_id: str):
        self._session_id = session_id
        self._task_id = task_id
        self._dao = get_redis_dao()

    def is_set(self) -> bool:
        return self._dao.is_stop_requested(self._session_id, self._task_id)


def _run_worker_loop() -> None:
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
            continue

        session_id = (payload.get('session_id') or '').strip()
        task_id = payload.get('task_id') or ''
        invocation_id = payload.get('invocation_id')
        user_prompt = payload.get('user_prompt') or ''
        mode = (payload.get('mode') or 'direct').strip().lower() or 'direct'
        resume_checkpoint = payload.get('resume_checkpoint')

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

        try:
            if not sessions_service.try_acquire_session_run(session_id):
                logger.warning(
                    'Agent worker: skip job session_id=%s task_id=%s reason=session_busy',
                    session_id,
                    task_id,
                )
                redis_dao.delete_confirmation_run_active(session_id)
                continue

            agent_run_service.run_agent_sync(
                session_id=session_id,
                user_prompt=user_prompt,
                send_cb=send_cb,
                loop=None,
                stop_event=stop_ev,
                mode=mode,
                reply_queue=reply_queue,
                task_id=task_id,
                invocation_id=invocation_id,
                resume_checkpoint=resume_checkpoint,
            )
        except Exception as e:
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
            redis_dao.delete_confirmation_run_active(session_id)
            redis_dao.delete_stop_requested(session_id, task_id)
            sessions_service.release_session_run(session_id)


def main() -> None:
    logging.basicConfig(
        format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
        level=logging.INFO,
    )

    def _on_sigterm(_signum: int, _frame: object) -> None:
        logger.info(
            'Agent worker: received SIGTERM, exit after current job (or BLPOP timeout).'
        )
        sys.exit(0)

    signal.signal(signal.SIGTERM, _on_sigterm)

    logger.info(
        'Agent worker: starting BLPOP loop queue_key=%s', 'chat:agent_run_queue'
    )
    _run_worker_loop()


if __name__ == '__main__':
    main()
