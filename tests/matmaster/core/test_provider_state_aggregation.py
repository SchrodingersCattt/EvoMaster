import pytest

from matmaster.types.messages import ProviderState, StreamChunk


class _FakeProvider:
    stream_timeout = 30.0
    stream_idle_timeout = 30.0
    max_retries = 1
    retry_delay = 0.0

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="hello")
        yield StreamChunk(finish_reason="stop")
        yield StreamChunk(
            provider_state=ProviderState(transport="fake", payload={"sig": "z"})
        )


@pytest.mark.asyncio
async def test_stream_llm_items_aggregates_provider_state():
    from types import SimpleNamespace

    from matmaster.core.agent_llm_stream import stream_llm_items

    kernel_resources = SimpleNamespace(llm_provider=_FakeProvider())
    final = None
    async for item in stream_llm_items(kernel_resources, [], None):
        if item.llm_response is not None:
            final = item.llm_response
    assert final is not None
    assert final.provider_state == ProviderState(transport="fake", payload={"sig": "z"})


@pytest.mark.asyncio
async def test_chat_completions_style_stream_leaves_provider_state_none():
    from types import SimpleNamespace

    from matmaster.core.agent_llm_stream import stream_llm_items

    class _PlainProvider(_FakeProvider):
        async def chat_stream(self, messages, tools=None, *, timeout=None):
            yield StreamChunk(content="hi")
            yield StreamChunk(finish_reason="stop")

    kernel_resources = SimpleNamespace(llm_provider=_PlainProvider())
    final = None
    async for item in stream_llm_items(kernel_resources, [], None):
        if item.llm_response is not None:
            final = item.llm_response
    assert final.provider_state is None


@pytest.mark.asyncio
async def test_anthropic_style_provider_state_overwrites_prior_none_chunks():
    from types import SimpleNamespace

    from matmaster.core.agent_llm_stream import stream_llm_items

    class _Provider:
        stream_timeout = 30.0
        stream_idle_timeout = 30.0
        max_retries = 1
        retry_delay = 0.0

        async def chat_stream(self, messages, tools=None, *, timeout=None):
            yield StreamChunk(reasoning_content="thinking")
            yield StreamChunk(content="answer")
            yield StreamChunk(finish_reason="stop")
            yield StreamChunk(
                provider_state=ProviderState(
                    transport="anthropic_messages",
                    payload={
                        "thinking": [
                            {
                                "type": "thinking",
                                "thinking": "thinking",
                                "signature": "sig",
                            }
                        ]
                    },
                )
            )

    final = None
    async for item in stream_llm_items(SimpleNamespace(llm_provider=_Provider()), [], None):
        if item.llm_response is not None:
            final = item.llm_response

    assert final is not None
    assert final.provider_state == ProviderState(
        transport="anthropic_messages",
        payload={
            "thinking": [
                {"type": "thinking", "thinking": "thinking", "signature": "sig"}
            ]
        },
    )
