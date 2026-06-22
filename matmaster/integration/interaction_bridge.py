from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from matmaster.types import InteractionRequestEvent

EventSink = Callable[[Any], Awaitable[None]]
DEFAULT_TIMEOUT_SECONDS = 1800


class InteractionBusyError(RuntimeError):
    """该 session 已有活跃交互占用 active 槽位，拒绝发起新交互。"""


class InteractionBridge:
    """通用 per-request 交互传输底座。对内层 payload 不透明。"""

    def __init__(
        self,
        *,
        session_id: str,
        task_id: str,
        invocation_id: str,
        event_sink: EventSink,
        dao: Any,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._session_id = session_id
        self._task_id = task_id
        self._invocation_id = invocation_id
        self._event_sink = event_sink
        self._dao = dao
        self._timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()

    async def emit(self, event: Any) -> None:
        await self._event_sink(event)

    async def request(
        self,
        *,
        kind: str,
        request_id: str,
        payload: dict,
        timeout_seconds: int | None = None,
    ) -> dict:
        """发起一次交互并阻塞等待回复 payload。"""
        timeout = self._timeout_seconds if timeout_seconds is None else timeout_seconds
        async with self._lock:
            if not self._dao.acquire_active_interaction(self._session_id, request_id):
                raise InteractionBusyError(
                    f"another interaction is active for session {self._session_id!r}"
                )
            try:
                expires_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=timeout)
                ).isoformat()
                self._dao.write_pending_interaction(
                    request_id,
                    {
                        "kind": kind,
                        "session_id": self._session_id,
                        "task_id": self._task_id,
                        "invocation_id": self._invocation_id,
                        "state": "pending",
                        "expires_at": expires_at,
                    },
                    ttl=timeout + 60,
                )
                await self._event_sink(
                    InteractionRequestEvent(
                        source="System",
                        kind=kind,
                        request_id=request_id,
                        task_id=self._task_id,
                        expires_at=expires_at,
                        payload=payload,
                    )
                )
                raw = await asyncio.to_thread(
                    self._dao.blpop_interaction_reply, request_id, timeout
                )
                if raw is None:
                    if self._dao.finalize_interaction(request_id, "timeout"):
                        raise TimeoutError(f"interaction {request_id!r} timed out")
                    raw = await asyncio.to_thread(
                        self._dao.blpop_interaction_reply, request_id, 5
                    )
                    if raw is None:
                        raise TimeoutError(f"interaction {request_id!r} timed out")
                if raw == "__CANCEL__":
                    raise asyncio.CancelledError(
                        f"interaction {request_id!r} cancelled"
                    )
                envelope = json.loads(raw)
                if (
                    envelope.get("request_id") != request_id
                    or envelope.get("kind") != kind
                ):
                    raise RuntimeError(
                        "interaction envelope mismatch: "
                        f"expected ({kind!r},{request_id!r}) "
                        f"got ({envelope.get('kind')!r},"
                        f"{envelope.get('request_id')!r})"
                    )
                return envelope.get("payload") or {}
            finally:
                self._dao.delete_interaction_reply(request_id)
                self._dao.release_active_interaction(self._session_id, request_id)
