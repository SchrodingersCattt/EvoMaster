"""Tests for AgentKernel invalid-finish diagnostics propagation."""

from __future__ import annotations

import pytest

from matmaster.types.events import AssistantStateEvent, RunResultEvent
from matmaster.types.messages import LLMResponse, StreamChunk

from .agent_kernel_test_helpers import _make_spec, _make_tool_registry


class ContentOnlyProvider:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None):
        return LLMResponse(content="not used", finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="hello ")
        yield StreamChunk(content="world")
        yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 5})


class EmptyStopProvider:
    stream_timeout = 10.0
    max_retries = 1
    retry_delay = 0.0

    def __init__(self, content: str | None = None, reasoning: str | None = None):
        self.content = content
        self.reasoning = reasoning

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None):
        return LLMResponse(content="not used", finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        if self.reasoning is not None:
            yield StreamChunk(reasoning_content=self.reasoning)
        if self.content is not None:
            yield StreamChunk(content=self.content)
        yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 10})


class LengthFinishProvider(ContentOnlyProvider):
    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="partial")
        yield StreamChunk(
            finish_reason="length",
            usage={"prompt_tokens": 10, "completion_tokens": 4096},
            usage_vendor={"outputTokens": 4096},
        )


class ContentFilterProvider(ContentOnlyProvider):
    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(finish_reason="content_filter")


class NonStopFinishProvider(ContentOnlyProvider):
    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="blocked by guardrail")
        yield StreamChunk(finish_reason="guardrail_intervened")


class ToolCallLengthProvider:
    def __init__(self) -> None:
        self.call_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None):
        return LLMResponse(content="not used", finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        self.call_count += 1
        if self.call_count == 1:
            yield StreamChunk(
                tool_call_deltas=[
                    {
                        "index": 0,
                        "id": "tc-length",
                        "name": "test_tool",
                        "arguments": '{"x": 1}',
                    }
                ]
            )
            yield StreamChunk(
                finish_reason="length",
                usage={"completion_tokens": 4096},
                usage_vendor={"outputTokens": 4096},
            )
        else:
            yield StreamChunk(content="done")
            yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 10})


@pytest.mark.asyncio
async def test_tool_call_length_finish_adds_assistant_state_detail(caplog) -> None:
    import logging

    from matmaster.core.agent import AgentKernel

    caplog.set_level(logging.WARNING, logger="matmaster.core.agent")
    registry, tools = _make_tool_registry(tool_names=["test_tool"])
    events = [
        event
        async for event in AgentKernel().run_stream(
            _make_spec(
                provider=ToolCallLengthProvider(),
                tool_registry=registry,
            ),
            "test task",
        )
    ]

    assistant_state = next(
        event for event in events if isinstance(event, AssistantStateEvent)
    )
    assert assistant_state.finish_detail is not None
    assert assistant_state.finish_detail.kind == "output_length_exceeded"
    assert assistant_state.finish_detail.has_tool_calls is True
    assert assistant_state.finish_detail.tool_call_count == 1
    assert assistant_state.finish_detail.truncation_risk is True
    assert tools[0].calls == [("test_tool", {"x": 1})]
    assert events[-1].reason == "natural"
    warning_records = [
        record
        for record in caplog.records
        if record.name == "matmaster.core.agent" and record.levelno == logging.WARNING
    ]
    assert any(
        record.getMessage().startswith("tool call response ended")
        and record.tool_names == ["test_tool"]
        and record.finish_detail["kind"] == "output_length_exceeded"
        for record in warning_records
    )


class TestInvalidFinishTerminalDetails:
    @pytest.mark.asyncio
    async def test_length_finish_sets_output_length_detail(self) -> None:
        from matmaster.core.agent import AgentKernel

        events = [
            event
            async for event in AgentKernel().run_stream(
                _make_spec(provider=LengthFinishProvider()),
                "test task",
            )
        ]

        result = events[-1]
        assert isinstance(result, RunResultEvent)
        assert result.status == "failed"
        assert result.reason == "invalid_finish"
        assert result.finish_detail is not None
        assert result.finish_detail.kind == "output_length_exceeded"
        assert result.finish_detail.last_turn_usage["completion_tokens"] == 4096

    @pytest.mark.asyncio
    async def test_content_filter_finish_sets_content_filtered_detail(self) -> None:
        from matmaster.core.agent import AgentKernel

        events = [
            event
            async for event in AgentKernel().run_stream(
                _make_spec(provider=ContentFilterProvider()),
                "test task",
            )
        ]

        assert events[-1].finish_detail.kind == "content_filtered"

    @pytest.mark.asyncio
    async def test_empty_stop_sets_empty_response_detail(self) -> None:
        from matmaster.core.agent import AgentKernel

        events = [
            event
            async for event in AgentKernel().run_stream(
                _make_spec(provider=EmptyStopProvider()),
                "test task",
            )
        ]

        assert events[-1].finish_detail.kind == "empty_response"

    @pytest.mark.asyncio
    async def test_reasoning_only_sets_reasoning_only_detail(self) -> None:
        from matmaster.core.agent import AgentKernel

        events = [
            event
            async for event in AgentKernel().run_stream(
                _make_spec(provider=EmptyStopProvider(reasoning="thinking only")),
                "test task",
            )
        ]

        assert events[-1].finish_detail.kind == "reasoning_only"

    @pytest.mark.asyncio
    async def test_unknown_provider_finish_sets_non_stop_finish_detail(self) -> None:
        from matmaster.core.agent import AgentKernel

        events = [
            event
            async for event in AgentKernel().run_stream(
                _make_spec(provider=NonStopFinishProvider()),
                "test task",
            )
        ]

        assert events[-1].finish_detail.kind == "non_stop_finish"
        assert events[-1].finish_detail.provider_finish_reason == (
            "guardrail_intervened"
        )


@pytest.mark.asyncio
async def test_missing_llm_response_terminal_sets_detail(monkeypatch) -> None:
    from matmaster.core.agent import AgentKernel, _KernelItem

    async def no_final_response(
        self, spec, api_messages, tool_defs, *, cancel_token=None
    ):
        if False:
            yield _KernelItem()

    kernel = AgentKernel()
    monkeypatch.setattr(
        AgentKernel,
        "_call_llm_streaming",
        no_final_response,
    )

    events = [
        event
        async for event in kernel.run_stream(
            _make_spec(provider=ContentOnlyProvider()),
            "test task",
        )
    ]

    assert events[-1].reason == "invalid_finish"
    assert events[-1].finish_detail.kind == "missing_llm_response"


class TestInvalidFinishDetailClassifier:
    def test_length_takes_priority_over_response_shape(self) -> None:
        from matmaster.core.finish_diagnostics import build_finish_detail

        response = LLMResponse(
            content="partial",
            reasoning_content="thinking",
            finish_reason="length",
            usage={"completion_tokens": 4096},
            usage_vendor={"outputTokens": 4096},
        )

        detail = build_finish_detail(response)

        assert detail.kind == "output_length_exceeded"
        assert detail.provider_finish_reason == "length"
        assert detail.has_visible_content is True
        assert detail.has_reasoning is True
        assert detail.last_turn_usage["completion_tokens"] == 4096
        assert detail.last_turn_usage_vendor["outputTokens"] == 4096
        assert detail.truncation_risk is True

    @pytest.mark.parametrize(
        ("response", "expected_kind"),
        [
            (
                LLMResponse(content=None, finish_reason="content_filter"),
                "content_filtered",
            ),
            (
                LLMResponse(
                    content=None,
                    reasoning_content="thinking",
                    finish_reason="stop",
                ),
                "reasoning_only",
            ),
            (
                LLMResponse(content=None, finish_reason="stop"),
                "empty_response",
            ),
            (
                LLMResponse(content="visible", finish_reason="guardrail_intervened"),
                "non_stop_finish",
            ),
            (
                LLMResponse(content="visible", finish_reason=None),
                "non_stop_finish",
            ),
        ],
    )
    def test_classifies_invalid_finish_matrix(
        self, response: LLMResponse, expected_kind: str
    ) -> None:
        from matmaster.core.finish_diagnostics import build_finish_detail

        detail = build_finish_detail(response)

        assert detail.kind == expected_kind
        assert detail.provider_finish_reason == response.finish_reason

    def test_missing_llm_response_api_shape_accepts_retry_metadata(self) -> None:
        from matmaster.core.finish_diagnostics import build_finish_detail
        from matmaster.types.errors import LLMError

        detail = build_finish_detail(
            None,
            attempts=3,
            last_error=LLMError(
                "stream failed",
                retryable=True,
                error_category="incomplete_response",
            ),
        )

        assert detail.kind == "missing_llm_response"
        assert detail.attempts == 3
        assert detail.last_error_kind == "incomplete_response"

    def test_classifier_fallback_returns_unknown(self, monkeypatch, caplog) -> None:
        import logging

        from matmaster.core import finish_diagnostics
        from matmaster.core.finish_diagnostics import build_finish_detail

        def raise_visible(_response: LLMResponse) -> bool:
            raise RuntimeError("boom")

        monkeypatch.setattr(
            finish_diagnostics,
            "_has_visible_content",
            raise_visible,
        )
        caplog.set_level(logging.WARNING, logger="matmaster.core.finish_diagnostics")

        detail = build_finish_detail(LLMResponse(content="x", finish_reason="stop"))

        assert detail.kind == "unknown"
        assert "finish detail classification failed" in caplog.text
