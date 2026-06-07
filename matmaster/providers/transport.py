"""Transport 基类（轴 C：实现复用脚手架）。

只收敛真正共享的部分：timeout/retry property + 生命周期骨架 + seam 声明。
本基类不实现 chat/chat_stream，因此不自满足 LLMProvider Protocol；满足 Protocol
的是具体子类。chat/chat_stream 不进基类：实际 API 调用与流式迭代在各 wire 协议间
差异过大，硬模板化会变坏抽象。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from matmaster.types.messages import LLMResponse, StreamChunk


class Transport:
    def __init__(
        self,
        *,
        timeout: float,
        stream_timeout: float | None = None,
        stream_idle_timeout: float | None = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        self._timeout = timeout
        self._stream_timeout = stream_timeout
        self._stream_idle_timeout = stream_idle_timeout
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._client: Any = None
        self._enter_count: int = 0

    @property
    def stream_timeout(self) -> float:
        return self._stream_timeout if self._stream_timeout is not None else self._timeout

    @property
    def stream_idle_timeout(self) -> float:
        return (
            self._stream_idle_timeout
            if self._stream_idle_timeout is not None
            else self._timeout
        )

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def retry_delay(self) -> float:
        return self._retry_delay

    async def __aenter__(self) -> "Transport":
        self._enter_count += 1
        if self._client is None:
            self._client = await self._open_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        self._enter_count -= 1
        if self._enter_count > 0:
            return
        if self._client is not None:
            await self._close_client(self._client)
            self._client = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            raise RuntimeError(
                "Transport must be used as async context manager: "
                "'async with transport:'"
            )
        return self._client

    async def _open_client(self) -> Any:
        raise NotImplementedError

    async def _close_client(self, client: Any) -> None:
        raise NotImplementedError

    def build_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        tool_choice: str | dict | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        """语义配置到该协议的请求 kwargs。"""
        raise NotImplementedError

    def convert_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """canonical 到 wire。"""
        raise NotImplementedError

    def normalize_response(self, raw: Any) -> LLMResponse:
        raise NotImplementedError

    def normalize_stream(self, raw_iter: Any) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError

    def classify_error(self, exc: Exception) -> Any:
        """SDK 异常到 LLMError；未知异常返回 None。"""
        raise NotImplementedError
