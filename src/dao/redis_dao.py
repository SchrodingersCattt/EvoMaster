"""Redis 连接与发布（DAO 层：仅负责与 Redis 的 I/O）。"""

import logging
from functools import lru_cache
from typing import Any, Optional

import redis

from src.utils.constant import REDIS_URL

logger = logging.getLogger(__name__)


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


@lru_cache(maxsize=1)
def get_redis_dao() -> RedisDao:
    return RedisDao()
