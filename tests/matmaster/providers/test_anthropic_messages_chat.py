from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from matmaster.providers.transports.anthropic_messages import AnthropicMessagesTransport
from matmaster.types.llm_provider import LLMProvider
from matmaster.types.messages import ProviderState, ToolCallData, UserMessage


def _usage(**kwargs):
    return SimpleNamespace(**kwargs)


class TestConstruction:
    def test_protocol_conformance(self) -> None:
        provider = AnthropicMessagesTransport(
            model="claude-opus-4-6",
            api_key="sk-test",
        )

        assert isinstance(provider, LLMProvider)
        assert provider.transport_tag == "anthropic_messages"

    async def test_client_uses_base_url_and_disables_sdk_retries(self) -> None:
        provider = AnthropicMessagesTransport(
            model="claude-opus-4-6",
            api_key="sk-test",
            base_url="https://proxy.example/anthropic",
            timeout=1200.0,
            stream_timeout=120.0,
            stream_idle_timeout=60.0,
        )
        with patch(
            "matmaster.providers.transports.anthropic_messages.anthropic.AsyncAnthropic"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value = mock_client

            async with provider:
                pass

            kwargs = mock_cls.call_args.kwargs
            assert kwargs["api_key"] == "sk-test"
            assert kwargs["base_url"] == "https://proxy.example/anthropic"
            assert kwargs["max_retries"] == 0
            assert kwargs["http_client"].timeout.read == 130.0


class TestNormalizeResponse:
    def test_extracts_text_tool_calls_thinking_state_usage_and_finish_reason(
        self,
    ) -> None:
        raw = SimpleNamespace(
            content=[
                SimpleNamespace(type="thinking", thinking="plan", signature="sig-1"),
                SimpleNamespace(type="text", text="hello"),
                SimpleNamespace(
                    type="tool_use",
                    id="toolu_1",
                    name="search",
                    input={"q": "x"},
                ),
            ],
            stop_reason="tool_use",
            usage=_usage(
                input_tokens=10,
                output_tokens=5,
                cache_read_input_tokens=3,
                cache_creation_input_tokens=4,
            ),
        )

        result = AnthropicMessagesTransport(
            model="claude-opus-4-6",
            api_key="sk-test",
        ).normalize_response(raw)

        assert result.content == "hello"
        assert result.reasoning_content == "plan"
        assert result.tool_calls == [
            ToolCallData(id="toolu_1", name="search", arguments={"q": "x"})
        ]
        assert result.finish_reason == "tool_calls"
        assert result.usage == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cache_read_tokens": 3,
            "cache_write_tokens": 4,
        }
        assert result.provider_state == ProviderState(
            transport="anthropic_messages",
            payload={
                "thinking": [
                    {"type": "thinking", "thinking": "plan", "signature": "sig-1"}
                ]
            },
        )
        assert result.usage_vendor is not None


class TestChat:
    async def test_chat_uses_stream_get_final_message(self) -> None:
        provider = AnthropicMessagesTransport(
            model="claude-opus-4-6",
            api_key="sk-test",
        )
        final = SimpleNamespace(content=[], stop_reason="end_turn", usage=None)
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=None)
        stream_cm.get_final_message = AsyncMock(return_value=final)
        mock_client = MagicMock()
        mock_client.messages.stream.return_value = stream_cm
        provider._client = mock_client

        result = await provider.chat([UserMessage(content="hi")], tool_choice="none")

        assert result.finish_reason == "stop"
        assert mock_client.messages.stream.call_args.kwargs["tool_choice"] == {
            "type": "none"
        }
        stream_cm.get_final_message.assert_awaited_once()
