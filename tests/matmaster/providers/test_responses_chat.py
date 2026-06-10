from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from matmaster.providers.transports.responses import ResponsesTransport
from matmaster.types.llm_provider import LLMProvider
from matmaster.types.messages import ProviderState, ToolCallData, UserMessage


class TestConstruction:
    def test_protocol_conformance(self) -> None:
        provider = ResponsesTransport(model="matmaster/gpt-5.5", api_key="sk-test")

        assert isinstance(provider, LLMProvider)
        assert provider.transport_tag == "responses"

    async def test_client_uses_base_url_and_disables_sdk_retries(self) -> None:
        provider = ResponsesTransport(
            model="matmaster/gpt-5.5",
            api_key="sk-test",
            base_url="https://proxy.example/v1",
            timeout=1200.0,
            stream_timeout=120.0,
            stream_idle_timeout=60.0,
        )
        with patch(
            "matmaster.providers.transports.openai_common.openai.AsyncOpenAI"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value = mock_client

            async with provider:
                pass

            kwargs = mock_cls.call_args.kwargs
            assert kwargs["api_key"] == "sk-test"
            assert kwargs["base_url"] == "https://proxy.example/v1"
            assert kwargs["max_retries"] == 0
            assert kwargs["http_client"].timeout.read == 130.0


def _part(**kwargs):
    return SimpleNamespace(**kwargs)


class _FakeReasoning:
    """模拟 SDK ResponseReasoningItem。"""

    type = "reasoning"

    def __init__(
        self, item_id: str, summary_texts: list[str], encrypted_content: str
    ) -> None:
        self.id = item_id
        self.summary = [
            SimpleNamespace(type="summary_text", text=t) for t in summary_texts
        ]
        self.encrypted_content = encrypted_content

    def model_dump(self, mode=None, exclude_none=False):
        return {
            "type": "reasoning",
            "id": self.id,
            "summary": [{"type": "summary_text", "text": p.text} for p in self.summary],
            "encrypted_content": self.encrypted_content,
        }


class TestNormalizeResponse:
    def test_extracts_content_reasoning_tools_state_usage_finish(self) -> None:
        raw = SimpleNamespace(
            output=[
                _FakeReasoning("rs_1", ["planning"], "enc"),
                SimpleNamespace(
                    type="message",
                    content=[_part(type="output_text", text="hello")],
                ),
                SimpleNamespace(
                    type="function_call",
                    call_id="call_1",
                    name="search",
                    arguments='{"q": "x"}',
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

        result = ResponsesTransport(
            model="matmaster/gpt-5.5", api_key="sk-test"
        ).normalize_response(raw)

        assert result.content == "hello"
        assert result.reasoning_content == "planning"
        assert result.tool_calls == [
            ToolCallData(id="call_1", name="search", arguments={"q": "x"})
        ]
        assert result.finish_reason == "tool_calls"
        assert result.usage == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cache_read_tokens": 2,
            "reasoning_tokens": 4,
        }
        assert result.provider_state == ProviderState(
            transport="responses",
            payload={
                "reasoning": [
                    {
                        "type": "reasoning",
                        "id": "rs_1",
                        "summary": [{"type": "summary_text", "text": "planning"}],
                        "encrypted_content": "enc",
                    }
                ]
            },
        )
        assert result.usage_vendor is not None

    def test_completed_without_function_call_is_stop(self) -> None:
        raw = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="message",
                    content=[_part(type="output_text", text="hi")],
                )
            ],
            status="completed",
            incomplete_details=None,
            usage=None,
        )

        result = ResponsesTransport(
            model="matmaster/gpt-5.5", api_key="sk-test"
        ).normalize_response(raw)

        assert result.finish_reason == "stop"
        assert result.provider_state is None

    def test_incomplete_max_output_tokens_is_length(self) -> None:
        raw = SimpleNamespace(
            output=[],
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            usage=None,
        )

        result = ResponsesTransport(
            model="matmaster/gpt-5.5", api_key="sk-test"
        ).normalize_response(raw)

        assert result.finish_reason == "length"


class TestChat:
    async def test_chat_uses_stream_get_final_response(self) -> None:
        provider = ResponsesTransport(model="matmaster/gpt-5.5", api_key="sk-test")
        final = SimpleNamespace(
            output=[], status="completed", incomplete_details=None, usage=None
        )
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=None)
        stream_cm.get_final_response = AsyncMock(return_value=final)
        mock_client = MagicMock()
        mock_client.responses.stream.return_value = stream_cm
        provider._client = mock_client

        result = await provider.chat([UserMessage(content="hi")], tool_choice="none")

        assert result.finish_reason == "stop"
        assert "tool_choice" not in mock_client.responses.stream.call_args.kwargs
        assert "stream" not in mock_client.responses.stream.call_args.kwargs
        stream_cm.get_final_response.assert_awaited_once()
