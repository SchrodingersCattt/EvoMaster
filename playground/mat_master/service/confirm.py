"""Confirmation/pause abstraction for the MatMaster playground backend.

Provides a small helper to emit a unified confirmation event and wait for a
human reply via an in-memory queue. This unifies planner confirmations and
ask-human prompts under a single event type.

Two wait modes are supported:
- timeout: emit confirmation_request (with countdown), wait up to timeout_sec,
  then emit confirmation_timeout and return the caller-supplied default.
- block: emit confirmation_request (no countdown), wait indefinitely until a
  reply arrives or the container is released (thread killed / queue GC'd).

The global enabled/disabled switch lives outside this class (in the callback
layer), so ConfirmationManager only ever handles the two active-wait modes.

Event contract:
- type: "confirmation_request"
- content payload: {
    "question": str,
    "mode": "timeout" | "block",
    "timeout_seconds"?: int,   # only present for mode=timeout
    "context"?: str,
    "actions"?: list[str],
    "origin"?: "planner" | "ask_human" | str,
  }

- type: "confirmation_timeout"
- content payload: { "question": str, "default_reply": str }

Reply contract (websocket -> server):
- type: "confirmation_reply"
- fields: { "content": str, "origin": str, "session_id": str }
"""

from __future__ import annotations

import queue
from enum import Enum
from typing import Callable, Iterable, Optional


class ConfirmMode(str, Enum):
    TIMEOUT = "timeout"
    BLOCK = "block"


class ConfirmationManager:
    """Emit a confirmation_request and block until confirmation_reply arrives."""

    def __init__(
        self,
        emitter: Callable[[str, str, dict], None],
        reply_queue: queue.Queue,
        *,
        default_timeout_sec: int = 20,
    ) -> None:
        # emitter(source, event_type, content)
        self._emit = emitter
        self._reply_queue = reply_queue
        self._default_timeout = int(default_timeout_sec) if default_timeout_sec and default_timeout_sec > 0 else 20

    def request(
        self,
        question: str,
        *,
        mode: ConfirmMode,
        timeout_sec: Optional[int] = None,
        default_reply: Optional[str] = None,
        context: Optional[str] = None,
        actions: Optional[Iterable[str]] = None,
        origin: Optional[str] = None,
        source_override: Optional[str] = None,
    ) -> Optional[str]:
        """Emit a confirmation_request event and wait for a reply.

        Args:
            question: The question to ask the human.
            mode: TIMEOUT waits up to timeout_sec then returns default_reply;
                  BLOCK waits indefinitely.
            timeout_sec: Seconds to wait in TIMEOUT mode (overrides constructor
                default). Ignored in BLOCK mode.
            default_reply: Value returned on timeout (TIMEOUT mode only).
                Used by Planner callers that parse the reply directly.
                LLM-path callers should leave this None and handle the None
                return themselves.
            context: Optional context string included in the event payload.
            actions: Optional list of suggested action strings.
            origin: Origin tag ("planner", "ask_human", etc.).
            source_override: Override the event source label.

        Returns:
            The user's reply string, or default_reply (TIMEOUT mode timeout),
            or None (BLOCK mode if container released without reply).
        """
        effective_timeout = timeout_sec if timeout_sec is not None else self._default_timeout

        payload: dict = {
            "question": question,
            "mode": mode.value,
        }
        if mode == ConfirmMode.TIMEOUT:
            payload["timeout_seconds"] = effective_timeout
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
            pass

        wait_timeout = None if mode == ConfirmMode.BLOCK else effective_timeout
        try:
            return self._reply_queue.get(timeout=wait_timeout)
        except queue.Empty:
            # Only TIMEOUT mode reaches here
            try:
                self._emit(source, "confirmation_timeout", {
                    "question": question,
                    "default_reply": default_reply or "",
                })
            except Exception:
                pass
            return default_reply
