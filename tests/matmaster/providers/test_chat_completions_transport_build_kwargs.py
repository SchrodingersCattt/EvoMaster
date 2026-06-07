"""ChatCompletionsTransport.build_kwargs：profile 平铺 reasoning 字段到 openai kwargs。"""

from __future__ import annotations

from matmaster.providers.transports.chat_completions import ChatCompletionsTransport
from matmaster.types.messages import UserMessage


def _t(**kw):
    base = dict(model="m", api_key="sk", timeout=10)
    base.update(kw)
    return ChatCompletionsTransport(**base)


def test_minimal_kwargs() -> None:
    t = _t(temperature=0.7)
    kw = t.build_kwargs([UserMessage(content="hi")], None)
    assert kw["model"] == "m"
    assert kw["temperature"] == 0.7
    assert kw["messages"] == [{"role": "user", "content": "hi"}]
    assert "reasoning_effort" not in kw
    assert "extra_body" not in kw
    assert "stream" not in kw


def test_reasoning_effort_goes_top_level() -> None:
    t = _t(reasoning_effort="High")
    kw = t.build_kwargs([], None)
    assert kw["reasoning_effort"] == "high"
    assert "extra_body" not in kw


def test_reasoning_summary_goes_extra_body_with_effort() -> None:
    t = _t(reasoning_effort="xhigh", reasoning_summary="detailed")
    kw = t.build_kwargs([], None)
    assert kw["reasoning_effort"] == "xhigh"
    assert kw["extra_body"] == {
        "reasoning": {"summary": "detailed", "effort": "xhigh"}
    }


def test_reasoning_summary_without_effort() -> None:
    t = _t(reasoning_summary="concise")
    kw = t.build_kwargs([], None)
    assert kw["extra_body"] == {"reasoning": {"summary": "concise"}}
    assert "reasoning_effort" not in kw


def test_max_tokens_and_tools_and_tool_choice() -> None:
    t = _t(max_tokens=128)
    tools = [{"type": "function", "function": {"name": "f"}}]
    kw = t.build_kwargs([], tools, tool_choice="none")
    assert kw["max_tokens"] == 128
    assert kw["tools"] == tools
    assert kw["tool_choice"] == "none"


def test_stream_sets_stream_and_include_usage() -> None:
    t = _t()
    kw = t.build_kwargs([], None, stream=True)
    assert kw["stream"] is True
    assert kw["stream_options"] == {"include_usage": True}
    assert "tool_choice" not in kw


def test_byok_extra_body_passthrough_user_wins() -> None:
    t = _t(
        reasoning_summary="auto",
        extra_body={"enable_thinking": True, "reasoning": {"summary": "x"}},
    )
    kw = t.build_kwargs([], None)
    assert kw["extra_body"]["reasoning"] == {"summary": "x"}
    assert kw["extra_body"]["enable_thinking"] is True


def test_convert_messages_returns_openai_wire_dicts() -> None:
    t = _t()
    msgs = [UserMessage(content="hi")]
    assert t.convert_messages(msgs) == [{"role": "user", "content": "hi"}]
