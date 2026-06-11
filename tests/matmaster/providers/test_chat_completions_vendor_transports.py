"""chat_completions vendor 子类：reasoning_content 回放 + vendor 请求字段。"""

from __future__ import annotations

from matmaster.providers.transports.chat_completions import (
    ChatCompletionsTransport,
    DeepSeekChatCompletionsTransport,
    QwenChatCompletionsTransport,
)
from matmaster.types.messages import (
    AssistantMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


def _convert(cls, messages):
    t = cls.__new__(cls)
    return t.convert_messages(messages)


def _replayed_payload(cls):
    return _convert(
        cls,
        [
            AssistantMessage(
                content="ans",
                reasoning_content="thought",
                tool_calls=[ToolCallData(id="c1", name="f", arguments={})],
            ),
            ToolMessage(content="ok", tool_call_id="c1", tool_name="f"),
        ],
    )[0]


def _t(cls, **kw):
    base = dict(model="m", api_key="sk", timeout=10)
    base.update(kw)
    return cls(**base)


def test_base_does_not_replay_reasoning_content() -> None:
    payload = _replayed_payload(ChatCompletionsTransport)
    assert "reasoning_content" not in payload


def test_deepseek_replays_reasoning_content() -> None:
    payload = _replayed_payload(DeepSeekChatCompletionsTransport)
    assert payload["reasoning_content"] == "thought"
    assert payload["content"] == "ans"
    assert payload["tool_calls"][0]["id"] == "c1"


def test_qwen_replays_reasoning_content() -> None:
    payload = _replayed_payload(QwenChatCompletionsTransport)
    assert payload["reasoning_content"] == "thought"


def test_replay_skips_when_reasoning_is_none() -> None:
    payload = _convert(
        DeepSeekChatCompletionsTransport, [AssistantMessage(content="ans")]
    )[0]
    assert "reasoning_content" not in payload


def test_qwen_build_kwargs_sends_preserve_thinking() -> None:
    kw = _t(QwenChatCompletionsTransport).build_kwargs(
        [UserMessage(content="hi")], None
    )
    assert kw["extra_body"] == {"preserve_thinking": True}


def test_base_and_deepseek_send_no_vendor_fields() -> None:
    for cls in (ChatCompletionsTransport, DeepSeekChatCompletionsTransport):
        kw = _t(cls).build_kwargs([UserMessage(content="hi")], None)
        assert "extra_body" not in kw


def test_explicit_extra_body_overrides_vendor_fields() -> None:
    kw = _t(
        QwenChatCompletionsTransport, extra_body={"preserve_thinking": False}
    ).build_kwargs([UserMessage(content="hi")], None)
    assert kw["extra_body"] == {"preserve_thinking": False}
