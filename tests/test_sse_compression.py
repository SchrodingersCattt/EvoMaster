"""SSE 流式 gzip 压缩测试。"""

from __future__ import annotations

import zlib

import pytest

from src.apis.sse_compression import gzip_sse_stream, should_gzip_sse


def _events(n: int = 40) -> list[str]:
    filler = "x" * 200
    return [
        'event: ag-ui\ndata: {"type":"tool_result","i":%d,"text":"%s"}\n\n'
        % (i, filler)
        for i in range(n)
    ]


async def _collect(events: list[str]) -> list[bytes]:
    async def src():
        for e in events:
            yield e

    return [chunk async for chunk in gzip_sse_stream(src())]


@pytest.mark.asyncio
async def test_gzip_sse_stream_roundtrip():
    events = _events()
    blob = b"".join(await _collect(events))
    assert zlib.decompress(blob, 16 + zlib.MAX_WBITS).decode() == "".join(events)


@pytest.mark.asyncio
async def test_gzip_sse_stream_reduces_size():
    events = _events()
    blob = b"".join(await _collect(events))
    raw = "".join(events).encode()
    assert len(blob) < len(raw) // 2  # 文本压缩至少减半


@pytest.mark.asyncio
async def test_gzip_sse_stream_incremental_decode():
    """每条事件后 Z_SYNC_FLUSH，消费端逐块喂入即可增量解出，保证 SSE 实时性。"""
    events = _events()
    chunks = await _collect(events)
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    # 第一块就应能解出内容（不必等整个流结束）
    assert len(d.decompress(chunks[0])) > 0
    got = b"" + d.decompress(b"")
    d2 = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = b""
    for c in chunks:
        out += d2.decompress(c)
    out += d2.flush()
    assert out.decode() == "".join(events)
    _ = got


@pytest.mark.asyncio
async def test_gzip_sse_stream_skips_empty_chunks():
    async def src():
        yield ""
        yield "data: a\n\n"
        yield None  # type: ignore[misc]
        yield "data: b\n\n"

    blob = b"".join([c async for c in gzip_sse_stream(src())])
    assert (
        zlib.decompress(blob, 16 + zlib.MAX_WBITS).decode() == "data: a\n\ndata: b\n\n"
    )


class _Req:
    def __init__(self, accept_encoding: str = ""):
        self.headers = {"accept-encoding": accept_encoding}


def test_should_gzip_sse_requires_client_support():
    assert should_gzip_sse(_Req("gzip, deflate, br")) is True
    assert should_gzip_sse(_Req("br")) is False
    assert should_gzip_sse(_Req("")) is False


def test_should_gzip_sse_env_disable(monkeypatch):
    monkeypatch.setenv("SSE_GZIP_ENABLED", "0")
    assert should_gzip_sse(_Req("gzip")) is False
    monkeypatch.setenv("SSE_GZIP_ENABLED", "1")
    assert should_gzip_sse(_Req("gzip")) is True
