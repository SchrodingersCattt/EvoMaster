from __future__ import annotations

from types import SimpleNamespace

from matmaster.providers.transports.anthropic_messages import AnthropicMessagesTransport
from matmaster.types.messages import ProviderState


async def _aiter(items):
    for item in items:
        yield item


def _event(event_type: str, **kwargs):
    return SimpleNamespace(type=event_type, **kwargs)


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
