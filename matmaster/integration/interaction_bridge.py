"""AskQuestion 的通信桥梁：发送 ask_question 事件到 SSE bus，
阻塞等待用户通过 reply queue 返回的 envelope。
"""

from __future__ import annotations

import asyncio
import json
import queue
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypedDict, runtime_checkable

from matmaster.types.events import AskQuestionEvent, AskQuestionTimeoutEvent, BusEvent

DEFAULT_TIMEOUT_SECONDS = 1800  # 30 分钟
EventSink = Callable[[BusEvent], Awaitable[None]]


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
    """发 ask_question bus 事件并等待结构化 reply envelope。"""

    def __init__(
        self,
        *,
        session_id: str,
        event_sink: EventSink,
        reply_queue: ReplyQueueLike,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._session_id = session_id
        self._event_sink = event_sink
        self._reply_queue = reply_queue
        self._timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()
        self._waiting_request_id: str | None = None

    def _wait_for_reply_sync(self, request_id: str) -> dict[str, Any]:
        """阻塞等待 reply queue 返回 envelope（同步，在 to_thread 中调用）。

        put_cancel() 送入的 None sentinel 会 raise CancelledError。
        """
        try:
            raw = self._reply_queue.get(timeout=self._timeout_seconds)
        except queue.Empty:
            raise TimeoutError(
                f"AskQuestion {request_id} timed out after {self._timeout_seconds}s"
            ) from None

        if raw is None:
            raise asyncio.CancelledError(f"AskQuestion {request_id} cancelled by user")

        envelope = json.loads(raw)
        payload = envelope.get("payload", envelope)
        if payload.get("request_id") != request_id:
            actual = payload.get("request_id")
            raise RuntimeError(
                "AskQuestion request_id mismatch: "
                f"expected={request_id!r} actual={actual!r}"
            )
        return envelope

    async def ask(
        self,
        *,
        request_id: str,
        questions: list[dict[str, Any]],
        metadata: dict[str, Any] | None,
    ) -> AskQuestionResponse:
        async with self._lock:
            self._waiting_request_id = request_id
            try:
                await self._event_sink(
                    AskQuestionEvent(
                        source="System",
                        request_id=request_id,
                        questions=questions,
                        metadata=metadata or {},
                        origin="tool:AskQuestion",
                        preview_format="markdown",
                    )
                )
                try:
                    envelope = await asyncio.to_thread(
                        self._wait_for_reply_sync,
                        request_id,
                    )
                except TimeoutError:
                    await self._event_sink(
                        AskQuestionTimeoutEvent(
                            source="System",
                            request_id=request_id,
                            questions=questions,
                            reason="timeout",
                        )
                    )
                    raise
                payload = envelope.get("payload", envelope)
                return {
                    "request_id": request_id,
                    "answers": payload.get("answers", {}),
                    "annotations": payload.get("annotations", {}),
                }
            finally:
                self._waiting_request_id = None
