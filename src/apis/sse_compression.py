"""SSE 响应的流式 gzip 压缩。

历史回放（尤其是工具调用密集的长会话）会一次性推送数 MB 的 JSON 文本
（tool_result / tool_call / thought 占绝大部分），裸传是加载耗时的主要来源。
本模块在 text/event-stream 输出层用 zlib 边压边发：每条事件后做 Z_SYNC_FLUSH，
浏览器对 Content-Encoding: gzip 透明解压，因此前端无需改动，且 LZ77 滑窗在
SYNC_FLUSH 边界间保留，逐事件 flush 不显著损失压缩率，又保持 SSE 的实时性。

可用环境变量 SSE_GZIP_ENABLED=0 关闭（网关异常时的快速回退开关）。
"""

from __future__ import annotations

import os
import zlib
from collections.abc import AsyncIterator

from starlette.requests import Request

# gzip 容器格式：16 + MAX_WBITS 让 zlib 产出带 gzip header/footer 的流。
_GZIP_WBITS = 16 + zlib.MAX_WBITS
_GZIP_LEVEL = 6


def _gzip_enabled() -> bool:
    return os.getenv('SSE_GZIP_ENABLED', '1').strip().lower() not in (
        '0',
        'false',
        'no',
        'off',
    )


def should_gzip_sse(request: Request) -> bool:
    """客户端声明接受 gzip 且未被环境变量关闭时才压缩。"""
    if not _gzip_enabled():
        return False
    accept = request.headers.get('accept-encoding', '')
    return 'gzip' in accept.lower()


async def gzip_sse_stream(
    source: AsyncIterator[str | bytes],
) -> AsyncIterator[bytes]:
    """把 SSE 的 str/bytes 生成器包成流式 gzip 字节生成器。

    每条事件压缩后 Z_SYNC_FLUSH，确保浏览器能立即增量解压；自然结束时再
    flush() 写出 gzip footer。客户端断开时 async for 被 GeneratorExit 打断，
    不在此处吞掉异常或额外 yield，交由上层正常关闭。
    """
    compressor = zlib.compressobj(_GZIP_LEVEL, zlib.DEFLATED, _GZIP_WBITS)
    async for chunk in source:
        if not chunk:
            continue
        data = chunk.encode('utf-8') if isinstance(chunk, str) else chunk
        if not data:
            continue
        out = compressor.compress(data) + compressor.flush(zlib.Z_SYNC_FLUSH)
        if out:
            yield out
    tail = compressor.flush()
    if tail:
        yield tail
