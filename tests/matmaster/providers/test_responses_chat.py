from __future__ import annotations

from unittest.mock import AsyncMock, patch

from matmaster.providers.transports.responses import ResponsesTransport
from matmaster.types.llm_provider import LLMProvider


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
            "matmaster.providers.transports.responses.openai.AsyncOpenAI"
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
