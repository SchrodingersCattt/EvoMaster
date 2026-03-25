"""Tests for matmaster.types.messages -- Message hierarchy and LLM response types."""

from __future__ import annotations

import json

import pytest

from matmaster.types.messages import (
    AssistantMessage,
    LLMResponse,
    Role,
    StreamChunk,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


# ── Role enum ──────────────────────────────────────────


class TestRole:
    def test_role_values(self) -> None:
        assert Role.SYSTEM == "system"
        assert Role.USER == "user"
        assert Role.ASSISTANT == "assistant"
        assert Role.TOOL == "tool"


# ── Message subtypes ──────────────────────────────────


class TestSystemMessage:
    def test_system_message_role(self) -> None:
        msg = SystemMessage(content="hello")
        assert msg.role == Role.SYSTEM

    def test_system_message_content(self) -> None:
        msg = SystemMessage(content="hello")
        assert msg.content == "hello"


class TestUserMessage:
    def test_user_message_role(self) -> None:
        msg = UserMessage(content="task")
        assert msg.role == Role.USER

    def test_user_message_content(self) -> None:
        msg = UserMessage(content="task")
        assert msg.content == "task"


class TestAssistantMessage:
    def test_assistant_message_role(self) -> None:
        msg = AssistantMessage(content="ok")
        assert msg.role == Role.ASSISTANT

    def test_assistant_message_with_tool_calls(self) -> None:
        tc = ToolCallData(id="tc1", name="fn", arguments={"a": 1})
        msg = AssistantMessage(content="ok", tool_calls=[tc])
        assert msg.tool_calls is not None
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "fn"

    def test_assistant_message_no_tool_calls_default(self) -> None:
        msg = AssistantMessage(content="ok")
        assert msg.tool_calls is None

    def test_assistant_message_reasoning_content(self) -> None:
        msg = AssistantMessage(content="ok", reasoning_content="thinking...")
        assert msg.reasoning_content == "thinking..."


class TestToolMessage:
    def test_tool_message_role(self) -> None:
        msg = ToolMessage(tool_call_id="tc1", tool_name="fn", content="result")
        assert msg.role == Role.TOOL

    def test_tool_message_fields(self) -> None:
        msg = ToolMessage(tool_call_id="tc1", tool_name="fn", content="result")
        assert msg.tool_call_id == "tc1"
        assert msg.tool_name == "fn"
        assert msg.content == "result"


# ── to_api_dict ───────────────────────────────────────


class TestToApiDict:
    def test_system_message_to_api_dict(self) -> None:
        msg = SystemMessage(content="sys")
        assert msg.to_api_dict() == {"role": "system", "content": "sys"}

    def test_user_message_to_api_dict(self) -> None:
        msg = UserMessage(content="ask")
        assert msg.to_api_dict() == {"role": "user", "content": "ask"}

    def test_assistant_message_with_tool_calls_to_api_dict(self) -> None:
        tc = ToolCallData(id="tc1", name="fn", arguments={"a": 1})
        msg = AssistantMessage(content="ok", tool_calls=[tc])
        d = msg.to_api_dict()
        assert d["role"] == "assistant"
        assert d["content"] == "ok"
        assert "tool_calls" in d
        assert len(d["tool_calls"]) == 1
        tc_dict = d["tool_calls"][0]
        assert tc_dict["id"] == "tc1"
        assert tc_dict["type"] == "function"
        assert tc_dict["function"]["name"] == "fn"
        assert json.loads(tc_dict["function"]["arguments"]) == {"a": 1}

    def test_assistant_message_without_tool_calls_to_api_dict(self) -> None:
        msg = AssistantMessage(content="ok")
        d = msg.to_api_dict()
        assert d["role"] == "assistant"
        assert d["content"] == "ok"
        assert "tool_calls" not in d

    def test_assistant_message_content_none_with_tool_calls(self) -> None:
        tc = ToolCallData(id="tc1", name="fn", arguments={"a": 1})
        msg = AssistantMessage(content=None, tool_calls=[tc])
        d = msg.to_api_dict()
        assert d["content"] is None

    def test_tool_message_to_api_dict(self) -> None:
        msg = ToolMessage(tool_call_id="tc1", tool_name="fn", content="result")
        d = msg.to_api_dict()
        assert d == {"role": "tool", "content": "result", "tool_call_id": "tc1"}
        # tool_name should NOT be in the API dict
        assert "tool_name" not in d


# ── ToolCallData ──────────────────────────────────────


class TestToolCallData:
    def test_tool_call_data_arguments_is_dict(self) -> None:
        tc = ToolCallData(id="tc1", name="fn", arguments={"key": "value"})
        assert isinstance(tc.arguments, dict)

    def test_tool_call_data_fields(self) -> None:
        tc = ToolCallData(id="tc1", name="fn", arguments={"a": 1})
        assert tc.id == "tc1"
        assert tc.name == "fn"
        assert tc.arguments == {"a": 1}


# ── LLMResponse ───────────────────────────────────────


class TestLLMResponse:
    def test_llm_response_with_tool_calls(self) -> None:
        tc = ToolCallData(id="tc1", name="fn", arguments={"a": 1})
        resp = LLMResponse(
            content="ok",
            tool_calls=[tc],
            finish_reason="tool_calls",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
        assert resp.content == "ok"
        assert resp.tool_calls is not None
        assert len(resp.tool_calls) == 1
        assert resp.finish_reason == "tool_calls"
        assert resp.usage["prompt_tokens"] == 10

    def test_llm_response_defaults(self) -> None:
        resp = LLMResponse()
        assert resp.content is None
        assert resp.reasoning_content is None
        assert resp.tool_calls is None
        assert resp.finish_reason is None
        assert resp.usage == {}


# ── StreamChunk ───────────────────────────────────────


class TestStreamChunk:
    def test_stream_chunk_full(self) -> None:
        chunk = StreamChunk(
            content="hello",
            reasoning_content="hmm",
            tool_call_deltas=[{"index": 0, "id": "tc1"}],
            finish_reason="stop",
            stream_state="streaming",
            stream_id="s1",
        )
        assert chunk.content == "hello"
        assert chunk.reasoning_content == "hmm"
        assert chunk.tool_call_deltas is not None
        assert len(chunk.tool_call_deltas) == 1
        assert chunk.finish_reason == "stop"
        assert chunk.stream_state == "streaming"
        assert chunk.stream_id == "s1"

    def test_stream_chunk_defaults(self) -> None:
        chunk = StreamChunk()
        assert chunk.content is None
        assert chunk.reasoning_content is None
        assert chunk.tool_call_deltas is None
        assert chunk.finish_reason is None
        assert chunk.stream_state is None
        assert chunk.stream_id is None


class TestStreamChunkUsage:
    def test_usage_default_none(self) -> None:
        chunk = StreamChunk(content="hello")
        assert chunk.usage is None

    def test_usage_round_trip(self) -> None:
        usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        chunk = StreamChunk(content="hello", usage=usage)
        assert chunk.usage == usage
        assert chunk.usage["prompt_tokens"] == 100
