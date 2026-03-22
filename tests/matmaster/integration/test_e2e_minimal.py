"""E2E pipeline test for minimal playground: simplest possible config.

No MCP, no Skill, no Bohrium -- verifies pipeline completes with natural finish.
All external dependencies mocked per D-10.
"""

from __future__ import annotations

import queue
from pathlib import Path
from typing import Any, Iterator

from matmaster.assembly.direct_exp import DirectExp
from matmaster.bus.queue import MessageBus
from matmaster.engine.agent import AgentKernel
from matmaster.engine.types import StreamChunk, LLMResponse
from matmaster.types.context import PlaygroundContext
from matmaster.types.events import FinishEvent, ThoughtEvent


class MinimalMockLLMProvider:
    """Minimal mock LLM: single-turn natural finish."""

    def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="minimal response", finish_reason="stop")

    def chat_with_retry(self, messages, tools=None, **kw) -> LLMResponse:
        return self.chat(messages, tools)

    def chat_stream(self, messages, tools=None) -> Iterator[StreamChunk]:
        yield StreamChunk(content="minimal response", finish_reason="stop")


def _make_minimal_ctx(tmp_path: Path) -> PlaygroundContext:
    """Create a minimal PlaygroundContext (no archival, no env vars)."""
    return PlaygroundContext(
        workdir=tmp_path / "workspace",
        session_type="local",
        cache_area=tmp_path / "cache",
    )


class TestMinimalE2EPipeline:
    """QUAL-02: Minimal E2E pipeline test -- simplest possible config."""

    def test_minimal_e2e_pipeline(self, tmp_path: Path) -> None:
        """E2E: Minimal playground with simplest possible config.
        No builtin_tools, no mcp_config, no skill_config.
        Verify pipeline completes with natural finish.
        """
        pg_ctx = _make_minimal_ctx(tmp_path)
        bus = MessageBus()
        mock_llm = MinimalMockLLMProvider()

        exp = DirectExp(
            llm_provider=mock_llm,
            bus=bus,
            # No MCP, no Skill
            mcp_config=None,
            skill_config=None,
        )
        spec = exp.assemble(pg_ctx)

        kernel = AgentKernel()
        finish = kernel.run(spec, "minimal test task")

        assert isinstance(finish, FinishEvent)
        assert finish.reason == "natural"
        assert finish.status == "completed"
        assert finish.final_content == "minimal response"

        # Bus should have received thought events from streaming
        events = []
        try:
            while True:
                events.append(bus.get(timeout=0.1))
        except queue.Empty:
            pass
        thought_events = [e for e in events if isinstance(e, ThoughtEvent)]
        assert len(thought_events) >= 1
