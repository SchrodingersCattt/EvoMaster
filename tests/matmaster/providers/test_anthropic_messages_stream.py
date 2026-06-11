from __future__ import annotations

from types import SimpleNamespace

from matmaster.providers.transports.anthropic_messages import AnthropicMessagesTransport
from matmaster.types.messages import ProviderState, UserMessage


async def _aiter(items):
    for item in items:
        yield item


def _event(event_type: str, **kwargs):
    return SimpleNamespace(type=event_type, **kwargs)


class _FakeStream:
    def __init__(self, items):
        self._items = iter(items)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration from None


class _FakeMessages:
    def __init__(self, items):
        self._items = items
        self.called_kwargs = None

    def stream(self, **kwargs):
        self.called_kwargs = kwargs
        return _FakeStream(self._items)


class TestNormalizeStream:
    async def test_stream_emits_reasoning_text_tool_delta_state_and_usage(
        self,
    ) -> None:
        provider = AnthropicMessagesTransport(
            model="claude-opus-4-6",
            api_key="sk-test",
        )
        events = [
            _event(
                "message_start",
                message=SimpleNamespace(
                    usage=SimpleNamespace(input_tokens=10, output_tokens=0)
                ),
            ),
            _event(
                "content_block_start",
                index=0,
                content_block=SimpleNamespace(type="thinking"),
            ),
            _event(
                "content_block_delta",
                index=0,
                delta=SimpleNamespace(type="thinking_delta", thinking="plan"),
            ),
            _event(
                "content_block_delta",
                index=0,
                delta=SimpleNamespace(type="signature_delta", signature="sig-1"),
            ),
            _event("content_block_stop", index=0),
            _event(
                "content_block_start",
                index=1,
                content_block=SimpleNamespace(type="text"),
            ),
            _event(
                "content_block_delta",
                index=1,
                delta=SimpleNamespace(type="text_delta", text="hello"),
            ),
            _event("content_block_stop", index=1),
            _event(
                "content_block_start",
                index=2,
                content_block=SimpleNamespace(
                    type="tool_use",
                    id="toolu_1",
                    name="search",
                ),
            ),
            _event(
                "content_block_delta",
                index=2,
                delta=SimpleNamespace(
                    type="input_json_delta",
                    partial_json='{"q"',
                ),
            ),
            _event(
                "content_block_delta",
                index=2,
                delta=SimpleNamespace(
                    type="input_json_delta",
                    partial_json=':"x"}',
                ),
            ),
            _event("content_block_stop", index=2),
            _event(
                "message_delta",
                delta=SimpleNamespace(stop_reason="tool_use"),
                usage=SimpleNamespace(output_tokens=5),
            ),
        ]

        chunks = [chunk async for chunk in provider.normalize_stream(_aiter(events))]

        assert chunks[0].reasoning_content == "plan"
        assert chunks[1].content == "hello"
        assert chunks[2].tool_call_deltas == [
            {"index": 0, "id": "toolu_1", "name": "search"}
        ]
        assert chunks[3].tool_call_deltas == [{"index": 0, "arguments": '{"q"'}]
        assert chunks[4].tool_call_deltas == [{"index": 0, "arguments": ':"x"}'}]
        assert chunks[5].finish_reason == "tool_calls"
        assert chunks[6].provider_state == ProviderState(
            transport="anthropic_messages",
            payload={
                "thinking": [
                    {
                        "type": "thinking",
                        "thinking": "plan",
                        "signature": "sig-1",
                    }
                ]
            },
        )
        assert chunks[7].usage == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }

    async def test_stream_preserves_redacted_thinking_provider_state(self) -> None:
        provider = AnthropicMessagesTransport(
            model="claude-opus-4-6",
            api_key="sk-test",
        )
        events = [
            _event(
                "content_block_start",
                index=0,
                content_block=SimpleNamespace(
                    type="redacted_thinking",
                    redacted_data="sealed",
                ),
            ),
            _event("content_block_stop", index=0),
        ]

        chunks = [chunk async for chunk in provider.normalize_stream(_aiter(events))]

        assert chunks[-1].provider_state == ProviderState(
            transport="anthropic_messages",
            payload={
                "thinking": [{"type": "redacted_thinking", "redacted_data": "sealed"}]
            },
        )

    async def test_stream_usage_includes_vendor_and_extended_token_fields(
        self,
    ) -> None:
        provider = AnthropicMessagesTransport(
            model="claude-opus-4-6",
            api_key="sk-test",
        )
        start_usage = SimpleNamespace(
            input_tokens=10,
            cache_read_input_tokens=2,
            cache_creation_input_tokens=3,
        )
        delta_usage = {
            "output_tokens": 5,
            "output_tokens_details": {"thinking_tokens": 4},
        }
        events = [
            _event(
                "message_start",
                message=SimpleNamespace(usage=start_usage),
            ),
            _event(
                "message_delta",
                delta=SimpleNamespace(stop_reason="end_turn"),
                usage=delta_usage,
            ),
        ]

        chunks = [chunk async for chunk in provider.normalize_stream(_aiter(events))]

        assert chunks[-1].usage == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cache_read_tokens": 2,
            "cache_write_tokens": 3,
            "reasoning_tokens": 4,
        }
        assert chunks[-1].usage_vendor is not None
        assert chunks[-1].usage_vendor["input_tokens"] == 10
        assert chunks[-1].usage_vendor["output_tokens"] == 5
        assert chunks[-1].usage_vendor["cache_read_input_tokens"] == 2
        assert chunks[-1].usage_vendor["cache_creation_input_tokens"] == 3
        assert chunks[-1].usage_vendor["output_tokens_details"] == {
            "thinking_tokens": 4
        }


class TestChatStream:
    async def test_chat_stream_uses_messages_stream_without_stream_kwarg(
        self,
    ) -> None:
        provider = AnthropicMessagesTransport(
            model="claude-opus-4-6",
            api_key="sk-test",
        )
        messages = _FakeMessages(
            [
                _event(
                    "message_delta",
                    delta=SimpleNamespace(stop_reason="end_turn"),
                )
            ]
        )
        provider._client = SimpleNamespace(messages=messages)

        chunks = [
            chunk
            async for chunk in provider.chat_stream(
                [UserMessage(content="hello")],
                timeout=12.5,
            )
        ]

        assert chunks[-1].finish_reason == "stop"
        assert "stream" not in messages.called_kwargs
        assert messages.called_kwargs["timeout"] == 12.5
