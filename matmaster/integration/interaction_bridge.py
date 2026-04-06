"""matmaster/integration/interaction_bridge.py

AskQuestion 的通信桥梁：发送 ask_question 事件到 SSE bus，
阻塞等待用户通过 reply queue 返回的 envelope。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any, Protocol, TypedDict, runtime_checkable

from matmaster.types.cancellation import CancellationToken
from matmaster.types.events import AskQuestionEvent, AskQuestionTimeoutEvent

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 1800  # 30 分钟


class AskQuestionResponse(TypedDict):
    request_id: str
    answers: dict[str, str]
    annotations: dict[str, dict[str, str]]


@runtime_checkable
class ReplyQueueLike(Protocol):
    def put_content(self, content: str) -> None: ...
    def put_cancel(self) -> None: ...
    def get(self, timeout: float | None = None) -> str | None: ...


class AskQuestionBridge:
    """发 ask_question 事件并等待 reply envelope。

    send_cb: 同步回调，用于把事件 model_dump() 推送到 SSE bus（由调用方注入）。
    reply_queue: ReplyQueueLike，bridge 从中 blpop 等待用户回复。
    """

    def __init__(
        self,
        *,
        session_id: str,
        send_cb: Callable[[dict[str, Any]], None],
        reply_queue: ReplyQueueLike,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._session_id = session_id
        self._send_cb = send_cb
        self._reply_queue = reply_queue
        self._timeout_seconds = timeout_seconds

    def _send_event(self, event: AskQuestionEvent | AskQuestionTimeoutEvent) -> None:
        """把事件 model_dump 推入 SSE bus。"""
        self._send_cb(event.model_dump())

    def _wait_for_reply(
        self,
        request_id: str,
        cancel_token: CancellationToken | None,
    ) -> dict[str, Any]:
        """阻塞等待 reply queue 返回 envelope（同步，在 to_thread 中调用）。

        超时或取消时发送 ask_question_timeout 事件并 raise。
        """
        import queue as _queue_mod

        try:
            raw = self._reply_queue.get(timeout=self._timeout_seconds)
        except _queue_mod.Empty:
            self._send_event(
                AskQuestionTimeoutEvent(
                    source="System",
                    request_id=request_id,
                    questions=[],
                    reason="timeout",
                )
            )
            raise TimeoutError(
                f"AskQuestion {request_id} timed out after {self._timeout_seconds}s"
            )

        if raw is None:
            # cancel sentinel
            raise asyncio.CancelledError(f"AskQuestion {request_id} cancelled by user")

        return json.loads(raw)

    async def ask(
        self,
        *,
        session_id: str,
        task_id: str,
        invocation_id: str | None,
        request_id: str,
        questions: list[dict[str, Any]],
        metadata: dict[str, Any] | None,
        cancel_token: CancellationToken | None,
    ) -> AskQuestionResponse:
        self._send_event(
            AskQuestionEvent(
                source="System",
                request_id=request_id,
                questions=questions,
                metadata=metadata or {},
                origin="tool:AskQuestion",
                preview_format="markdown",
            )
        )
        envelope = await asyncio.to_thread(
            self._wait_for_reply, request_id, cancel_token
        )
        payload = envelope.get("payload", envelope)
        return {
            "request_id": request_id,
            "answers": payload.get("answers", {}),
            "annotations": payload.get("annotations", {}),
        }
