"""Redis 连接与发布（DAO 层：仅负责与 Redis 的 I/O）。
另提供 confirmation_reply 多 worker 用：run_active 标记、run_context（task_id/invocation_id）、回复 list 的读写。
"""

import json
import logging
from functools import lru_cache
from typing import Any, Optional

import redis

from src.utils.constant import REDIS_URL

logger = logging.getLogger(__name__)

# confirmation_reply 多 worker：Redis key 与取消占位值
CONFIRMATION_RUN_ACTIVE_KEY = 'chat:run_active:{session_id}'
CONFIRMATION_RUN_CONTEXT_KEY = 'chat:run_context:{session_id}'
CONFIRMATION_REPLY_LIST_KEY = 'chat:confirmation_reply:{session_id}'
CONFIRMATION_CANCEL_VALUE = '__CANCEL__'
CONFIRMATION_RUN_ACTIVE_TTL_SEC = 3600


def _run_active_key(session_id: str) -> str:
    return CONFIRMATION_RUN_ACTIVE_KEY.format(session_id=session_id.strip())


def _run_context_key(session_id: str) -> str:
    return CONFIRMATION_RUN_CONTEXT_KEY.format(session_id=session_id.strip())


def _reply_list_key(session_id: str) -> str:
    return CONFIRMATION_REPLY_LIST_KEY.format(session_id=session_id.strip())


class RedisDao:
    """Redis 访问：发布与创建连接。未配置 REDIS_URL 时各方法返回 None/False。"""

    def __init__(self) -> None:
        self._publish_client: Optional[Any] = None

    def get_publish_client(self) -> Optional[Any]:
        """进程内单例，用于 publish。"""
        if not REDIS_URL:
            return None
        if self._publish_client is None:
            self._publish_client = self._make_client()
        return self._publish_client

    def create_client(self) -> Optional[Any]:
        """每次新建连接（供订阅线程等独立连接使用）。"""
        return self._make_client()

    def publish(self, channel: str, message: str) -> bool:
        """向指定 channel 发布一条消息。成功返回 True，未配置或失败返回 False。"""
        client = self.get_publish_client()
        if client is None:
            return False
        try:
            client.publish(channel, message)
            return True
        except Exception as e:
            logger.warning('Redis publish failed channel=%s: %s', channel, e)
            return False

    @staticmethod
    def _make_client() -> Optional[Any]:
        if not REDIS_URL:
            return None
        try:
            return redis.from_url(REDIS_URL, decode_responses=True)
        except Exception as e:
            logger.warning('Redis client init failed: %s', e)
            return None

    # ---------- confirmation_reply 多 worker（run_active + reply list）----------

    def set_confirmation_run_active(self, session_id: str) -> bool:
        """标记该会话当前有活跃 run。未配置 Redis 或失败返回 False。"""
        client = self.create_client()
        if not client:
            return False
        try:
            client.set(
                _run_active_key(session_id),
                '1',
                ex=CONFIRMATION_RUN_ACTIVE_TTL_SEC,
            )
            return True
        except Exception as e:
            logger.warning(
                'Redis set run_active failed session_id=%s: %s', session_id, e
            )
            return False

    def delete_confirmation_run_active(self, session_id: str) -> None:
        """清除 run 活跃标记与 run_context。"""
        client = self.create_client()
        if not client:
            return
        try:
            client.delete(_run_active_key(session_id))
            client.delete(_run_context_key(session_id))
        except Exception as e:
            logger.warning(
                'Redis delete run_active/run_context failed session_id=%s: %s',
                session_id,
                e,
            )

    def set_confirmation_run_context(
        self, session_id: str, task_id: str, invocation_id: str
    ) -> bool:
        """写入当前 run 的 task_id / invocation_id，供 broadcast_reply 跨 worker 使用。"""
        client = self.create_client()
        if not client:
            return False
        try:
            client.set(
                _run_context_key(session_id),
                json.dumps(
                    {'task_id': task_id, 'invocation_id': invocation_id},
                    ensure_ascii=False,
                ),
                ex=CONFIRMATION_RUN_ACTIVE_TTL_SEC,
            )
            return True
        except Exception as e:
            logger.warning(
                'Redis set run_context failed session_id=%s: %s', session_id, e
            )
            return False

    def get_confirmation_run_context(self, session_id: str) -> Optional[dict]:
        """读取当前 run 的 task_id / invocation_id，无或失败返回 None。"""
        client = self.create_client()
        if not client:
            return None
        try:
            raw = client.get(_run_context_key(session_id))
            if not raw:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning(
                'Redis get run_context failed session_id=%s: %s', session_id, e
            )
            return None

    def is_confirmation_run_active(self, session_id: str) -> bool:
        """是否配置了 Redis 且该会话在 Redis 中有活跃 run。"""
        client = self.create_client()
        if not client:
            return False
        try:
            return client.exists(_run_active_key(session_id)) > 0
        except Exception:
            return False

    def delete_confirmation_reply_list(self, session_id: str) -> None:
        """清空该会话的回复列表（新 run 开始时调用）。"""
        client = self.create_client()
        if not client:
            return
        try:
            client.delete(_reply_list_key(session_id))
        except Exception as e:
            logger.warning(
                'Redis clear reply list failed session_id=%s: %s', session_id, e
            )

    def rpush_confirmation_reply(self, session_id: str, value: str) -> None:
        """向该会话回复列表尾部推入一条（内容或取消占位）。"""
        client = self.create_client()
        if not client:
            return
        try:
            client.rpush(_reply_list_key(session_id), value)
        except Exception as e:
            logger.warning(
                'Redis RPUSH confirmation_reply failed session_id=%s: %s',
                session_id,
                e,
            )

    def blpop_confirmation_reply(
        self, session_id: str, timeout_sec: int
    ) -> Optional[str]:
        """从该会话回复列表左侧阻塞弹出一条。超时返回 None；否则返回字符串（可能为取消占位）。"""
        client = self.create_client()
        if not client:
            return None
        try:
            result = client.blpop(_reply_list_key(session_id), timeout=timeout_sec)
        except Exception as e:
            logger.warning(
                'Redis BLPOP confirmation_reply failed session_id=%s: %s',
                session_id,
                e,
            )
            return None
        if result is None:
            return None
        _, value = result
        return value


@lru_cache(maxsize=1)
def get_redis_dao() -> RedisDao:
    return RedisDao()
