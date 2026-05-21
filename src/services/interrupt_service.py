"""Interrupt checker implementation backed by Redis.

Provides the InterruptChecker protocol for the agent kernel to check
whether the user has queued messages and wants to interrupt tool dispatch.
"""

import asyncio
import logging

from src.dao.redis_dao import get_redis_dao

logger = logging.getLogger(__name__)


class RedisInterruptChecker:
    """Checks Redis for interrupt hint/confirm signals."""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id

    def has_hint(self) -> bool:
        return get_redis_dao().has_interrupt_hint(self._session_id)

    async def wait_for_confirm(self, timeout: float) -> bool:
        """Poll for interrupt confirm with short intervals up to timeout."""
        elapsed = 0.0
        interval = 0.1
        while elapsed < timeout:
            if get_redis_dao().has_interrupt_confirm(self._session_id):
                return True
            if not get_redis_dao().has_interrupt_hint(self._session_id):
                return False
            await asyncio.sleep(interval)
            elapsed += interval
        return False

    def cleanup(self) -> None:
        get_redis_dao().cleanup_interrupt_keys(self._session_id)
