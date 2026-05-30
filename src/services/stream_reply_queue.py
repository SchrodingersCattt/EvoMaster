"""RedisReplyQueue for interaction replies across API and worker processes."""

from __future__ import annotations

import queue

from src.dao.redis_dao import INTERACTION_CANCEL_VALUE, get_redis_dao


class RedisReplyQueue:
    """基于 Redis List 的回复队列，任意 worker 可 put_content/put_cancel，执行 run 的 worker 可 get。"""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id.strip()
        self._dao = get_redis_dao()

    def put_content(self, content: str) -> None:
        self._dao.rpush_interaction_reply(self._session_id, content)

    def put_cancel(self) -> None:
        self._dao.rpush_interaction_reply(self._session_id, INTERACTION_CANCEL_VALUE)

    def get(self, timeout: float | None = None) -> str | None:
        # timeout=None 表示 BLOCK 模式，Redis BLPOP timeout=0 表示一直阻塞
        sec = 0 if timeout is None else int(timeout) if timeout >= 0 else 300
        value = self._dao.blpop_interaction_reply(self._session_id, sec)
        if value is None:
            raise queue.Empty
        if value == INTERACTION_CANCEL_VALUE:
            return None
        return value
