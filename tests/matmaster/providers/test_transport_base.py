"""Transport 基类：property 回退 + 生命周期脚手架 + seam 抛 NotImplementedError。

用一个最小具体子类（只实现 _open_client/_close_client）验证基类行为；
基类本身不实现 chat/chat_stream，故不自满足 LLMProvider Protocol。
"""

from __future__ import annotations

import pytest

from matmaster.providers.transport import Transport


class _FakeClient:
    def __init__(self) -> None:
        self.closed = False


class _MiniTransport(Transport):
    """只补生命周期钩子，用于测基类脚手架。"""

    async def _open_client(self) -> _FakeClient:
        return _FakeClient()

    async def _close_client(self, client: _FakeClient) -> None:
        client.closed = True


def test_stream_timeout_falls_back_to_timeout() -> None:
    t = _MiniTransport(timeout=300)
    assert t.stream_timeout == 300
    assert t.stream_idle_timeout == 300


def test_stream_timeout_uses_explicit_values() -> None:
    t = _MiniTransport(timeout=300, stream_timeout=120, stream_idle_timeout=60)
    assert t.stream_timeout == 120
    assert t.stream_idle_timeout == 60


def test_retry_properties() -> None:
    t = _MiniTransport(timeout=10, max_retries=5, retry_delay=2.0)
    assert t.max_retries == 5
    assert t.retry_delay == 2.0


def test_ensure_client_requires_context_manager() -> None:
    t = _MiniTransport(timeout=10)
    with pytest.raises(RuntimeError, match="async context manager"):
        t._ensure_client()


@pytest.mark.asyncio
async def test_lifecycle_open_and_close() -> None:
    t = _MiniTransport(timeout=10)
    async with t as entered:
        assert entered is t
        client = t._ensure_client()
        assert client.closed is False
    assert t._client is None
    assert client.closed is True


@pytest.mark.asyncio
async def test_reentrant_context_manager_opens_once() -> None:
    t = _MiniTransport(timeout=10)
    async with t:
        first = t._ensure_client()
        async with t:
            assert t._ensure_client() is first
        assert t._ensure_client() is first
    assert t._client is None


def test_base_seams_raise_not_implemented() -> None:
    t = _MiniTransport(timeout=10)
    with pytest.raises(NotImplementedError):
        t.build_kwargs([], None)
    with pytest.raises(NotImplementedError):
        t.convert_messages([])
    with pytest.raises(NotImplementedError):
        t.normalize_response(object())
    with pytest.raises(NotImplementedError):
        t.classify_error(Exception("x"))
