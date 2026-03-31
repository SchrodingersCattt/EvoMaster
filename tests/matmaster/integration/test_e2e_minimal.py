"""E2E pipeline test for minimal playground: simplest possible config.

No MCP, no Skill, no Bohrium -- verifies pipeline completes with natural finish.
All external dependencies mocked per D-10.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from matmaster.config.exp import ExpConfig
from matmaster.core.agent import AgentKernel
from matmaster.core.bus import MessageBus
from matmaster.core.exp import Exp
from matmaster.types.context import PlaygroundContext
from matmaster.types.events import ResponseEvent
from matmaster.types.messages import LLMResponse, StreamChunk
from matmaster.types.runtime import KernelResult


class MinimalMockLLMProvider:
    """Minimal mock LLM: single-turn natural finish."""

    async def __aenter__(self) -> MinimalMockLLMProvider:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="minimal response", finish_reason="stop")

    async def chat_stream(
        self, messages, tools=None, *, timeout=None
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content="minimal response", finish_reason="stop")


def _make_minimal_ctx(tmp_path: Path, llm_provider: Any = None) -> PlaygroundContext:
    """Create a minimal PlaygroundContext (no archival, no env vars)."""
    return PlaygroundContext(
        workdir=tmp_path / "workspace",
        session_type="local",
        cache_area=tmp_path / "cache",
        llm_provider=llm_provider,
    )


class TestMinimalE2EPipeline:
    """QUAL-02: Minimal E2E pipeline test -- simplest possible config."""

    async def test_minimal_e2e_pipeline(self, tmp_path: Path) -> None:
        """E2E: Minimal playground with simplest possible config.
        No builtin_tools, no mcp_config, no skill_config.
        Verify pipeline completes with natural finish.
        """
        mock_llm = MinimalMockLLMProvider()
        pg_ctx = _make_minimal_ctx(tmp_path, llm_provider=mock_llm)
        bus = MessageBus()

        config = ExpConfig(name="direct")
        exp = Exp(config)
        runtime = await exp.build_runtime(pg_ctx, bus=bus)

        kernel = AgentKernel()
        finish = await kernel.run(runtime.spec, "minimal test task")

        assert isinstance(finish.result, KernelResult)
        assert finish.result.reason == "natural"
        assert finish.result.status == "completed"
        assert finish.result.final_content == "minimal response"

        # Bus should have received response events from streaming content
        events = []
        try:
            while True:
                events.append(bus.get_nowait())
        except asyncio.QueueEmpty:
            pass
        response_events = [e for e in events if isinstance(e, ResponseEvent)]
        assert len(response_events) >= 1
