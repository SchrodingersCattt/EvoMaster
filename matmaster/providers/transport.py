"""Transport 基类（轴 C：实现复用脚手架）。

只收敛真正共享的部分：timeout/retry property + 生命周期骨架 + seam 声明。
本基类不实现 chat/chat_stream，因此不自满足 LLMProvider Protocol；满足 Protocol
的是具体子类。chat/chat_stream 不进基类：实际 API 调用与流式迭代在各 wire 协议间
差异过大，硬模板化会变坏抽象。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from matmaster.types.messages import (
    AssistantMessage,
    LLMResponse,
    Message,
    StreamChunk,
    ToolMessage,
)


def dump_model_to_jsonable(value: Any) -> Any:
    """SDK 对象尽力转 JSON 可序列化 dict：pydantic model_dump 优先，dict 复制，
    其余扫描简单属性。"""
    if value is None:
        return None
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json", exclude_none=True)
        except TypeError:
            return model_dump(exclude_none=True)
    if isinstance(value, dict):
        return dict(value)
    out: dict[str, Any] = {}
    for key in dir(value):
        if key.startswith("_"):
            continue
        try:
            item = getattr(value, key)
        except Exception:
            continue
        if isinstance(item, (str, int, float, bool, type(None), dict, list)):
            out[key] = item
    return out


def tool_image_relay_label(message: ToolMessage) -> str:
    """工具图片中继为 user 内容时的前导标签（各 wire 协议共用同一文案）。"""
    return f"[Images from {message.tool_name} (tool_call {message.tool_call_id})]"


class Transport:
    transport_tag: str = ""

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
        return (
            self._stream_timeout if self._stream_timeout is not None else self._timeout
        )

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

    async def __aenter__(self) -> Transport:
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

    def _build_http_client(self) -> Any:
        """各 SDK client 共用的 httpx.AsyncClient（read 超时随流式 idle 放宽）。"""
        import httpx

        read_t = float(max(self.stream_idle_timeout, self.stream_timeout) + 10)
        return httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=read_t, write=30.0, pool=15.0)
        )

    def _claim_provider_state(self, msg: AssistantMessage) -> dict[str, Any] | None:
        """tag 匹配则返回不透明 payload，否则 None（跨协议丢弃回放状态）。"""
        state = msg.provider_state
        if state is None or state.transport != self.transport_tag:
            return None
        return state.payload

    async def _open_client(self) -> Any:
        raise NotImplementedError

    async def _close_client(self, client: Any) -> None:
        raise NotImplementedError

    def build_kwargs(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        *,
        tool_choice: str | dict | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        """语义配置到该协议的请求 kwargs。"""
        raise NotImplementedError

    def convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """canonical 到 wire。"""
        raise NotImplementedError

    def normalize_response(self, raw: Any) -> LLMResponse:
        raise NotImplementedError

    def normalize_stream(self, raw_iter: Any) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError

    def classify_error(self, exc: Exception) -> Any:
        """SDK 异常到 LLMError；未知异常返回 None。"""
        raise NotImplementedError
