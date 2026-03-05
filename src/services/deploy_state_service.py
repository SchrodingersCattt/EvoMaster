"""部署状态服务：利用 Redis 记录各会话最近一次运行所属的服务版本。"""

import logging
from functools import lru_cache
from typing import Optional, Tuple

from src.dao.redis_dao import RedisDao, get_redis_dao
from src.utils.build_info import get_build_version

logger = logging.getLogger(__name__)


class DeployStateService:
    """封装与 Redis 交互的部署状态逻辑。"""

    _SESSION_VERSION_HASH = 'matmaster_chat:session_versions'

    def __init__(self, redis_dao: Optional[RedisDao] = None) -> None:
        self._redis_dao = redis_dao or get_redis_dao()
        self._current_version = get_build_version()

    def _get_client(self):
        return self._redis_dao.get_publish_client() or self._redis_dao.create_client()

    def get_current_version(self) -> str:
        return self._current_version

    def record_session_version(self, session_id: str) -> None:
        """记录该会话当前 run 所属的版本。无 Redis 配置时静默返回。"""
        client = self._get_client()
        if client is None:
            return
        sid = session_id.strip()
        if not sid:
            return
        try:
            client.hset(self._SESSION_VERSION_HASH, sid, self._current_version)
            logger.info(
                'record_session_version: session_id=%s version=%s',
                sid,
                self._current_version,
            )
        except Exception as exc:
            logger.warning(
                'DeployStateService.record_session_version failed sid=%s: %s',
                sid,
                exc,
            )

    def get_last_session_version(self, session_id: str) -> Optional[str]:
        client = self._get_client()
        if client is None:
            return None
        sid = session_id.strip()
        if not sid:
            return None
        try:
            value = client.hget(self._SESSION_VERSION_HASH, sid)
        except Exception as exc:
            logger.warning(
                'DeployStateService.get_last_session_version failed sid=%s: %s',
                sid,
                exc,
            )
            return None
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    def classify_restart_reason(self, session_id: str) -> Tuple[str, dict]:
        """
        根据上一轮版本与当前版本判断 run_interrupted 原因：
        - 上一次版本缺失或不同 => deploy（升级导致）
        - 相同版本 => restart（同版本进程重启导致）
        """
        previous_version = self.get_last_session_version(session_id)
        detail = {
            'current_version': self._current_version,
            'previous_version': previous_version,
        }
        if previous_version and previous_version == self._current_version:
            reason = 'restart'
        else:
            reason = 'deploy'
            if previous_version is None:
                detail['note'] = 'missing_previous_version'
        logger.info(
            'classify_restart_reason: session_id=%s reason=%s previous_version=%s current_version=%s',
            session_id.strip(),
            reason,
            previous_version,
            self._current_version,
        )
        return reason, detail


@lru_cache
def get_deploy_state_service() -> DeployStateService:
    return DeployStateService()
