"""Confirmation/pause abstraction for the MatMaster playground backend.

Provides a small helper to emit a unified confirmation event and wait for a
human reply via an in-memory queue. This unifies planner confirmations and
ask-human prompts under a single event type.

Event contract:
- type: "confirmation_request"
- content payload: {
    "question": str,
    "context"?: str,
    "actions"?: list[str],
    "origin"?: "planner" | "ask_human" | str,
  }

Reply contract (websocket -> server):
- type: "confirmation_reply"
- fields: { "content": str, "origin": str, "session_id": str }
"""

from __future__ import annotations

import queue
from typing import Callable, Iterable, Optional


class ConfirmationManager:
    """Emit a confirmation_request and block until confirmation_reply arrives."""

    def __init__(
        self,
        emitter: Callable[[str, str, dict], None],
        reply_queue: queue.Queue,
        *,
        timeout_sec: int = 300,
    ) -> None:
        # emitter(source, event_type, content)
        self._emit = emitter
        self._reply_queue = reply_queue
        self._timeout = int(timeout_sec) if timeout_sec and timeout_sec > 0 else 300

    def request(
        self,
        question: str,
        *,
        context: Optional[str] = None,
        actions: Optional[Iterable[str]] = None,
        origin: Optional[str] = None,
        source_override: Optional[str] = None,
    ) -> Optional[str]:
        payload: dict = {"question": question}
        if context:
            payload["context"] = context
        if actions:
            payload["actions"] = list(actions)
        if origin:
            payload["origin"] = origin

        source = source_override or ("Planner" if origin == "planner" else "MatMaster")
        try:
            self._emit(source, "confirmation_request", payload)
        except Exception:
            # Best-effort emit; still wait for a reply in case UI didn't receive it
            pass

        try:
            reply = self._reply_queue.get(timeout=self._timeout)
            return reply
        except queue.Empty:
            return None
