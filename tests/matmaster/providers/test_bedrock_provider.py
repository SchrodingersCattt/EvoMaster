"""Unit tests for Bedrock message mapping and build_provider (bedrock)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from matmaster.config.llm import LLMConfig, LLMProfileConfig, LLMRouteConfig
from matmaster.providers.bedrock_provider import (
    BedrockProvider,
    _openai_tools_to_bedrock,
    openai_messages_to_bedrock_converse,
)
from matmaster.providers.llm_factory import build_provider


def test_openai_messages_to_bedrock_basic() -> None:
    system, msgs = openai_messages_to_bedrock_converse(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ]
    )
    assert system == [{"text": "sys"}]
    assert msgs == [{"role": "user", "content": [{"text": "hello"}]}]


def test_openai_messages_tool_round_trip() -> None:
    system, msgs = openai_messages_to_bedrock_converse(
        [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "echo",
                            "arguments": '{"x": 1}',
                        },
                    }
                ],
            },
            {"role": "tool", "content": "ok", "tool_call_id": "call_1"},
        ]
    )
    assert system is None
    assert msgs[1]["role"] == "assistant"
    tu = msgs[1]["content"][0]["toolUse"]
    assert tu["toolUseId"] == "call_1"
    assert tu["name"] == "echo"
    assert tu["input"] == {"x": 1}
    tr = msgs[2]["content"][0]["toolResult"]
    assert tr["toolUseId"] == "call_1"
    assert tr["content"][0]["text"] == "ok"


def test_openai_tools_to_bedrock() -> None:
    cfg = _openai_tools_to_bedrock(
        [
            {
                "type": "function",
                "function": {
                    "name": "fn",
                    "description": "d",
                    "parameters": {
                        "type": "object",
                        "properties": {"a": {"type": "string"}},
                    },
                },
            }
        ]
    )
    assert cfg is not None
    spec = cfg["tools"][0]["toolSpec"]
    assert spec["name"] == "fn"
    assert spec["inputSchema"]["json"]["type"] == "object"


def test_build_provider_bedrock() -> None:
    cfg = LLMConfig(
        profiles={
            "opus_br": LLMProfileConfig(
                provider="bedrock",
                model="anthropic.claude-3-opus-20240229-v1:0",
                bedrock_region="us-west-2",
                api_key="",
                base_url=None,
                temperature=1.0,
                model_family="claude-4.6",
            ),
        },
        routes={"bedrock-opus": LLMRouteConfig(profile="opus_br")},
        default="opus_br",
    )
    p = build_provider(cfg, model_override="bedrock-opus")
    from matmaster.providers.bedrock_provider import BedrockProvider

    assert isinstance(p, BedrockProvider)
    assert p._model_id == "anthropic.claude-3-opus-20240229-v1:0"
    assert p._region == "us-west-2"


def test_build_provider_bedrock_region_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    cfg = LLMConfig(
        profiles={
            "b": LLMProfileConfig(
                provider="bedrock",
                model="m1",
                api_key="",
            ),
        },
        default="b",
    )
    p = build_provider(cfg)
    from matmaster.providers.bedrock_provider import BedrockProvider

    assert isinstance(p, BedrockProvider)
    assert p._region == "eu-west-1"


async def test_bedrock_chat_rejects_unsupported_tool_choice() -> None:
    provider = BedrockProvider(model_id="m1", region="us-west-2")

    with pytest.raises(NotImplementedError, match="tool_choice='auto'"):
        await provider.chat(
            [{"role": "user", "content": "hi"}],
            tools=[],
            tool_choice="auto",
        )


async def test_bedrock_chat_none_tool_choice_preserves_tools(monkeypatch) -> None:
    provider = BedrockProvider(model_id="m1", region="us-west-2")
    captured = {}

    def fake_converse(**kwargs):
        captured.update(kwargs)
        return {
            "output": {"message": {"content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
        }

    client = MagicMock()
    client.converse.side_effect = fake_converse
    provider._client = client

    result = await provider.chat(
        [{"role": "user", "content": "hi"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "paper_search",
                    "description": "Search",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        tool_choice="none",
    )

    assert result.content == "ok"
    assert "toolConfig" in captured
    assert "toolChoice" not in captured.get("toolConfig", {})
