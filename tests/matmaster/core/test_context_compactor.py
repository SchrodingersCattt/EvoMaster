"""Tests for ContextCompactor."""

from __future__ import annotations

import pytest

from matmaster.types.messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


class TestEstimateTokens:
    """Token estimation for messages."""

    def test_empty_list(self) -> None:
        from matmaster.core.context_compactor import estimate_tokens

        assert estimate_tokens([]) == 0

    def test_single_message(self) -> None:
        from matmaster.core.context_compactor import estimate_tokens

        msgs = [UserMessage(content="hello world")]
        tokens = estimate_tokens(msgs)
        assert tokens > 0
        assert tokens < 20

    def test_multiple_messages(self) -> None:
        from matmaster.core.context_compactor import estimate_tokens

        msgs = [
            SystemMessage(content="You are helpful"),
            UserMessage(content="What is 2+2?"),
            AssistantMessage(content="4"),
        ]
        total = estimate_tokens(msgs)
        single = estimate_tokens([msgs[0]])
        assert total > single

    def test_tool_message_content(self) -> None:
        from matmaster.core.context_compactor import estimate_tokens

        long_result = "x" * 4000
        msgs = [ToolMessage(content=long_result, tool_call_id="tc-1", tool_name="t")]
        tokens = estimate_tokens(msgs)
        assert tokens > 500
