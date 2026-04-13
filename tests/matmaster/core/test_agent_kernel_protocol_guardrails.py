from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from matmaster.core.agent import AgentKernel
from matmaster.types.errors import LLMError
from matmaster.types.messages import StreamChunk

from .agent_kernel_test_helpers import _make_spec, _make_tool_registry


class _DuplicateIdProvider:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None):
        raise AssertionError("unused")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(
            tool_call_deltas=[
                {
                    "index": 0,
                    "id": "tc-dup",
                    "name": "test_tool",
                    "arguments": '{"x": 1}',
                }
            ]
        )
        yield StreamChunk(
            tool_call_deltas=[
                {
                    "index": 1,
                    "id": "tc-dup",
                    "name": "test_tool",
                    "arguments": '{"x": 1}',
                }
            ]
        )
        yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 1})


class TestKernelToolProtocolGuardrails:
    @pytest.mark.asyncio
    async def test_duplicate_tool_call_ids_fail_before_execute_batch(self) -> None:
        provider = _DuplicateIdProvider()
        registry, _ = _make_tool_registry(tool_names=["test_tool"])
        spec = _make_spec(provider=provider, tool_registry=registry)
        mock_runner = MagicMock()
        mock_runner.execute_batch = AsyncMock(return_value=[])
        spec = spec.model_copy(update={"tool_runner": mock_runner})

        kernel = AgentKernel()
        with pytest.raises(LLMError, match="duplicate tool_call ids"):
            async for _event in kernel.run_stream(spec, "test task"):
                pass

        assert mock_runner.execute_batch.await_count == 0
