"""ChatCompletionsTransport normalize_response / normalize_stream 返回形状。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from matmaster.providers.transports.chat_completions import ChatCompletionsTransport
from matmaster.types.messages import LLMResponse, StreamChunk


def _t():
    return ChatCompletionsTransport(model="m", api_key="sk", timeout=10)


async def _aiter(items):
    for it in items:
        yield it


def _usage(prompt=10, completion=5, total=15):
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        prompt_tokens_details=None,
        completion_tokens_details=None,
        model_dump=lambda **_: {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        },
    )


def test_normalize_response_text_and_usage() -> None:
    message = SimpleNamespace(content="hello", tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    raw = SimpleNamespace(choices=[choice], usage=_usage())
    out = _t().normalize_response(raw)
    assert isinstance(out, LLMResponse)
    assert out.content == "hello"
    assert out.tool_calls is None
    assert out.finish_reason == "stop"
    assert out.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    assert out.usage_vendor == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }


def test_normalize_response_tool_calls() -> None:
    tc = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="search", arguments='{"q": "x"}'),
    )
    message = SimpleNamespace(content=None, tool_calls=[tc])
    choice = SimpleNamespace(message=message, finish_reason="tool_calls")
    raw = SimpleNamespace(choices=[choice], usage=None)
    out = _t().normalize_response(raw)
    assert out.tool_calls is not None
    assert out.tool_calls[0].id == "call_1"
    assert out.tool_calls[0].name == "search"
    assert out.tool_calls[0].arguments == {"q": "x"}


@pytest.mark.asyncio
async def test_normalize_stream_yields_content_then_usage() -> None:
    delta = SimpleNamespace(content="hi", reasoning_content=None, tool_calls=None)
    chunk1 = SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None
    )
    chunk2 = SimpleNamespace(choices=[], usage=_usage())
    out = []
    async for sc in _t().normalize_stream(_aiter([chunk1, chunk2])):
        out.append(sc)
    assert all(isinstance(x, StreamChunk) for x in out)
    assert out[0].content == "hi"
    assert out[-1].usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    assert out[-1].usage_vendor is not None
