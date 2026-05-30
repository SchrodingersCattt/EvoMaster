"""E2E pipeline test for minimal playground: simplest possible config.

No MCP, no Skill, no Bohrium -- verifies pipeline completes with natural finish.
All external dependencies mocked per D-10.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from matmaster.config.exp import ExpConfig
from matmaster.core.agent import AgentKernel
from matmaster.core.exp import Exp
from matmaster.core.playground import ExecutionEnvironment
from matmaster.core.run_context import AgentRunContext, AgentRunRequest
from matmaster.types.events import ResponseEvent, RunResultEvent
from matmaster.types.messages import LLMResponse, StreamChunk


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


def _make_minimal_ctx(tmp_path: Path, llm_provider: Any = None) -> AgentRunContext:
    """Create a minimal AgentRunContext (no archival, no env vars)."""
    return AgentRunContext(
        environment=ExecutionEnvironment(
            workdir=tmp_path / "workspace",
            session_type="local",
            cache_area=tmp_path / "cache",
        ),
        request=AgentRunRequest(llm_provider=llm_provider),
    )


class TestMinimalE2EPipeline:
    """QUAL-02: Minimal E2E pipeline test -- simplest possible config."""

    async def test_minimal_e2e_pipeline(self, tmp_path: Path) -> None:
        """E2E: Minimal playground with simplest possible config.
        No builtin_tools, no mcp_config, no skill_config.
        Verify pipeline completes with natural finish via run_stream().
        """
        mock_llm = MinimalMockLLMProvider()
        agent_run_ctx = _make_minimal_ctx(tmp_path, llm_provider=mock_llm)

        config = ExpConfig(name="direct")
        exp = Exp(config)
        runtime = await exp.build_runtime(agent_run_ctx)

        # Collect events from kernel.run_stream() generator
        kernel = AgentKernel()
        events = []
        async for event in kernel.run_stream(runtime.kernel_runtime, "minimal test task"):
            events.append(event)

        # Generator should have emitted ResponseEvent(s)
        response_events = [e for e in events if isinstance(e, ResponseEvent)]
        assert len(response_events) >= 1

        # Terminal event should be RunResultEvent with natural finish
        run_results = [e for e in events if isinstance(e, RunResultEvent)]
        assert len(run_results) == 1
        assert run_results[0].status == "completed"
        assert run_results[0].reason == "natural"
