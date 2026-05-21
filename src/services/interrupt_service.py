"""Interrupt checker implementation backed by Redis.

Provides the InterruptChecker protocol for the agent kernel to check
whether the user has queued messages and wants to interrupt tool dispatch.
"""

import asyncio
import logging

from src.dao.redis_dao import RedisDao, get_redis_dao

logger = logging.getLogger(__name__)


class RedisInterruptChecker:
    """Checks Redis for interrupt hint/confirm signals.

    Caches a single Redis client for the lifetime of the checker
    to avoid creating ~60 connections during the 3s polling window.
    """

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._client = get_redis_dao().create_client()

    def has_hint(self) -> bool:
        if not self._client:
            return False
        try:
            from src.dao.redis_dao import _interrupt_hint_key

            return self._client.exists(_interrupt_hint_key(self._session_id)) > 0
        except Exception:
            return False

    async def wait_for_confirm(self, timeout: float) -> bool:
        """Poll for interrupt confirm with short intervals up to timeout."""
        if not self._client:
            return False
        from src.dao.redis_dao import _interrupt_confirm_key, _interrupt_hint_key

        confirm_key = _interrupt_confirm_key(self._session_id)
        hint_key = _interrupt_hint_key(self._session_id)
        elapsed = 0.0
        interval = 0.1
        while elapsed < timeout:
            try:
                if self._client.exists(confirm_key) > 0:
                    return True
                if self._client.exists(hint_key) == 0:
                    return False
            except Exception:
                return False
            await asyncio.sleep(interval)
            elapsed += interval
        return False

    def cleanup(self) -> None:
        get_redis_dao().cleanup_interrupt_keys(self._session_id)
