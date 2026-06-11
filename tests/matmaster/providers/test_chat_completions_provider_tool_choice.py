from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from matmaster.providers.transports.chat_completions import ChatCompletionsTransport
from matmaster.types.messages import UserMessage


def _make_mock_completion(content: str = "summary") -> MagicMock:
    mock = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    choice.message.tool_calls = None
    choice.message.reasoning_content = None
    choice.finish_reason = "stop"
    mock.choices = [choice]
    mock.usage = None
    return mock


async def test_chat_forwards_tool_choice() -> None:
    provider = ChatCompletionsTransport(model="gpt-4o-mini", api_key="sk-test")
    mock_client = AsyncMock()
    mock_client.chat.completions.create.return_value = _make_mock_completion()
    provider._client = mock_client

    result = await provider.chat(
        [UserMessage(content="Summarize")],
        tools=[{"type": "function", "function": {"name": "paper_search"}}],
        tool_choice="none",
    )

    assert result.content == "summary"
    kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert kwargs["tool_choice"] == "none"
    assert kwargs["tools"] == [
        {"type": "function", "function": {"name": "paper_search"}}
    ]
