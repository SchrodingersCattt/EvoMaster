from __future__ import annotations

from types import SimpleNamespace

import pytest

from matmaster.providers.transports.responses import ResponsesTransport
from matmaster.types.errors import LLMError
from matmaster.types.messages import ProviderState, UserMessage


async def _aiter(items):
    for item in items:
        yield item


def _event(event_type: str, **kwargs):
    return SimpleNamespace(type=event_type, **kwargs)


def _part(**kwargs):
    return SimpleNamespace(**kwargs)


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


class _FakeResponses:
    def __init__(self, items):
        self._items = items
        self.called_kwargs = None

    def stream(self, **kwargs):
        self.called_kwargs = kwargs
        return _FakeStream(self._items)


class TestNormalizeStream:
    async def test_stream_emits_content_reasoning_tool_state_usage_finish(
        self,
    ) -> None:
        provider = ResponsesTransport(model="matmaster/gpt-5.5", api_key="sk-test")
        reasoning_item = SimpleNamespace(
            type="reasoning",
            id="rs_1",
            summary=[{"type": "summary_text", "text": "plan"}],
            encrypted_content="enc",
        )
        completed_response = SimpleNamespace(
            output=[
                reasoning_item,
                SimpleNamespace(
                    type="function_call",
                    call_id="call_1",
                    name="search",
                    arguments="{}",
                ),
            ],
            status="completed",
            incomplete_details=None,
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                input_tokens_details=SimpleNamespace(cached_tokens=2),
                output_tokens_details=SimpleNamespace(reasoning_tokens=4),
            ),
        )
        events = [
            _event("response.reasoning_summary_text.delta", delta="plan"),
            _event("response.output_text.delta", delta="hello"),
            _event(
                "response.output_item.added",
                item=SimpleNamespace(
                    type="function_call",
                    id="fc_1",
                    call_id="call_1",
                    name="search",
                ),
            ),
            _event("response.function_call_arguments.delta", item_id="fc_1", delta='{"q"'),
            _event("response.function_call_arguments.delta", item_id="fc_1", delta=':"x"}'),
            _event("response.completed", response=completed_response),
        ]

        chunks = [c async for c in provider.normalize_stream(_aiter(events))]

        assert chunks[0].reasoning_content == "plan"
        assert chunks[1].content == "hello"
        assert chunks[2].tool_call_deltas == [
            {"index": 0, "id": "call_1", "name": "search"}
        ]
        assert chunks[3].tool_call_deltas == [{"index": 0, "arguments": '{"q"'}]
        assert chunks[4].tool_call_deltas == [{"index": 0, "arguments": ':"x"}'}]
        assert chunks[5].provider_state == ProviderState(
            transport="responses",
            payload={
                "reasoning": [
                    {
                        "type": "reasoning",
                        "id": "rs_1",
                        "summary": [{"type": "summary_text", "text": "plan"}],
                        "encrypted_content": "enc",
                    }
                ]
            },
        )
        assert chunks[6].finish_reason == "tool_calls"
        assert chunks[7].usage == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cache_read_tokens": 2,
            "reasoning_tokens": 4,
        }
        assert chunks[7].usage_vendor is not None

    async def test_reasoning_text_delta_maps_to_reasoning_content(self) -> None:
        provider = ResponsesTransport(model="matmaster/gpt-5.5", api_key="sk-test")
        events = [_event("response.reasoning_text.delta", delta="raw")]

        chunks = [c async for c in provider.normalize_stream(_aiter(events))]

        assert chunks[0].reasoning_content == "raw"

    async def test_refusal_delta_becomes_content_and_completed_sets_content_filter(
        self,
    ) -> None:
        provider = ResponsesTransport(model="matmaster/gpt-5.5", api_key="sk-test")
        completed = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="message",
                    content=[_part(type="refusal", refusal="no")],
                )
            ],
            status="completed",
            incomplete_details=None,
            usage=None,
        )
        events = [
            _event("response.refusal.delta", delta="I refuse"),
            _event("response.completed", response=completed),
        ]

        chunks = [c async for c in provider.normalize_stream(_aiter(events))]

        assert chunks[0].content == "I refuse"
        assert any(c.finish_reason == "content_filter" for c in chunks)

    async def test_failed_event_raises_classified_server_error(self) -> None:
        provider = ResponsesTransport(model="matmaster/gpt-5.5", api_key="sk-test")
        failed = SimpleNamespace(error=SimpleNamespace(code="server_error", message="boom"))
        events = [_event("response.failed", response=failed)]

        with pytest.raises(LLMError) as exc_info:
            [c async for c in provider.normalize_stream(_aiter(events))]

        assert exc_info.value.error_category == "server"

    async def test_failed_bad_request_reasoning_replay_is_non_retryable(self) -> None:
        provider = ResponsesTransport(model="matmaster/gpt-5.5", api_key="sk-test")
        failed = SimpleNamespace(
            error=SimpleNamespace(
                code="bad_request",
                message="reasoning item rs_1 without its required following item",
            )
        )
        events = [_event("response.failed", response=failed)]

        with pytest.raises(LLMError) as exc_info:
            [c async for c in provider.normalize_stream(_aiter(events))]

        assert exc_info.value.retryable is False
        assert exc_info.value.error_category == "bad_request"


class TestChatStream:
    async def test_chat_stream_uses_responses_stream_without_stream_kwarg(self) -> None:
        provider = ResponsesTransport(model="matmaster/gpt-5.5", api_key="sk-test")
        completed = SimpleNamespace(
            output=[], status="completed", incomplete_details=None, usage=None
        )
        responses = _FakeResponses([_event("response.completed", response=completed)])
        provider._client = SimpleNamespace(responses=responses)

        chunks = [
            c
            async for c in provider.chat_stream(
                [UserMessage(content="hi")], timeout=12.5
            )
        ]

        assert any(c.finish_reason == "stop" for c in chunks)
        assert "stream" not in responses.called_kwargs
        assert responses.called_kwargs["timeout"] == 12.5
