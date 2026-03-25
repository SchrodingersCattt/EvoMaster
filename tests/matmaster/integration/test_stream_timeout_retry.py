"""Integration test: full chain from build_provider through kernel retry."""

from __future__ import annotations

from typing import Any, Iterator
from unittest.mock import MagicMock, patch

import openai
import pytest

from matmaster.config.llm import LLMConfig, LLMProfileConfig, LLMRouteConfig
from matmaster.core.agent import AgentKernel
from matmaster.providers.llm_factory import build_provider
from matmaster.types.errors import LLMError
from matmaster.types.messages import StreamChunk, UserMessage
from matmaster.types.runtime import AgentRuntimeSpec


def _make_stream_chunk(content=None, finish_reason=None):
    mock = MagicMock()
    choice = MagicMock()
    choice.delta.content = content
    choice.delta.reasoning_content = None
    choice.delta.tool_calls = None
    choice.finish_reason = finish_reason
    mock.choices = [choice]
    mock.usage = None
    return mock


class TestStreamTimeoutRetryIntegration:
    def test_provider_retries_through_kernel(self) -> None:
        """Timeout in chat_stream -> LLMError -> kernel retries -> success."""
        call_count = 0

        def fake_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise openai.APITimeoutError(request=MagicMock())
            return iter([
                _make_stream_chunk(content="answer", finish_reason="stop"),
            ])

        config = LLMConfig(
            profiles={
                "test": LLMProfileConfig(
                    model="test-model",
                    api_key="sk-test",
                    timeout=1200.0,
                    stream_timeout=10.0,
                    stream_idle_timeout=5.0,
                    max_retries=3,
                    retry_delay=0.0,
                ),
            },
            routes={},
            default="test",
        )

        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.chat.completions.create.side_effect = fake_create

            provider = build_provider(config)
            assert provider.stream_timeout == 10.0

            spec = AgentRuntimeSpec(
                llm_provider=provider,
                system_prompt="test",
            )
            kernel = AgentKernel()
            response = kernel._call_llm(spec, [UserMessage(content="hi")])

            assert response.content == "answer"
            assert call_count == 2
