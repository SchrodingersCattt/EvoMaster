"""多 worker 下 session run 归属与 worker 存活注册，用于区分「任务在别的 pod 上跑」与「真的重启」。"""

import logging
from functools import lru_cache
from typing import Optional

from src.dao.redis_dao import get_redis_dao

logger = logging.getLogger(__name__)

# Redis key 与 TTL
SESSION_RUN_OWNER_KEY_PREFIX = 'matmaster_chat:session_run_owner:'
SESSION_RUN_OWNER_TTL_SEC = 7200
WORKER_ALIVE_KEY_PREFIX = 'matmaster_chat:worker_alive:'
WORKER_ALIVE_TTL_SEC = 30


def _session_run_owner_key(session_id: str) -> str:
    return SESSION_RUN_OWNER_KEY_PREFIX + (session_id or '').strip()


def _worker_alive_key(worker_id: str) -> str:
    return WORKER_ALIVE_KEY_PREFIX + (worker_id or '').strip()


class WorkerRegistryService:
    """Session run owner 与 worker 存活标记的读写，依赖 Redis。"""

    def set_session_run_owner(self, session_id: str, worker_id: str) -> bool:
        """记录该会话当前 run 所在 worker。acquire 时调用；未配置 Redis 或失败返回 False。"""
        client = get_redis_dao().create_client()
        if not client:
            return False
        sid = (session_id or '').strip()
        if not sid:
            return False
        try:
            client.set(
                _session_run_owner_key(sid),
                worker_id,
                ex=SESSION_RUN_OWNER_TTL_SEC,
            )
            logger.info(
                'set_session_run_owner: session_id=%s worker_id=%s',
                sid,
                worker_id,
            )
            return True
        except Exception as e:
            logger.warning('set_session_run_owner failed session_id=%s: %s', sid, e)
            return False

    def get_session_run_owner(self, session_id: str) -> Optional[str]:
        """返回该会话当前 run 所在 worker_id，无或失败返回 None。"""
        client = get_redis_dao().create_client()
        if not client:
            return None
        sid = (session_id or '').strip()
        if not sid:
            return None
        try:
            value = client.get(_session_run_owner_key(sid))
            return (value or '').strip() or None
        except Exception as e:
            logger.warning('get_session_run_owner failed session_id=%s: %s', sid, e)
            return None

    def delete_session_run_owner(self, session_id: str) -> None:
        """清除该会话的 run owner。release 时调用。"""
        client = get_redis_dao().create_client()
        if not client:
            return
        sid = (session_id or '').strip()
        if not sid:
            return
        try:
            client.delete(_session_run_owner_key(sid))
            logger.info('delete_session_run_owner: session_id=%s', sid)
        except Exception as e:
            logger.warning('delete_session_run_owner failed session_id=%s: %s', sid, e)

    def set_worker_alive(self, worker_id: str) -> bool:
        """刷新本进程存活标记（lifespan 里周期调用）。TTL 较短，重启后旧进程不再刷新即失效。"""
        client = get_redis_dao().create_client()
        if not client:
            return False
        wid = (worker_id or '').strip()
        if not wid:
            return False
        try:
            client.set(
                _worker_alive_key(wid),
                '1',
                ex=WORKER_ALIVE_TTL_SEC,
            )
            return True
        except Exception as e:
            logger.warning('set_worker_alive failed worker_id=%s: %s', wid, e)
            return False

    def is_worker_alive(self, worker_id: str) -> bool:
        """该 worker 的存活 key 是否仍存在（未过期）。用于区分「别的 pod 在跑」与「已重启的旧 pid」。"""
        client = get_redis_dao().create_client()
        if not client:
            return False
        wid = (worker_id or '').strip()
        if not wid:
            return False
        try:
            return client.exists(_worker_alive_key(wid)) > 0
        except Exception as e:
            logger.warning('is_worker_alive failed worker_id=%s: %s', wid, e)
            return False


@lru_cache(maxsize=1)
def get_worker_registry_service() -> WorkerRegistryService:
    return WorkerRegistryService()
