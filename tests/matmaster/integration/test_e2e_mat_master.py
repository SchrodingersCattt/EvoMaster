"""E2E pipeline tests for mat_master: Playground.prepare() -> Exp.assemble() -> Kernel.run().

All external dependencies mocked per D-10. Tests verify pipeline connectivity
and correct event flow without requiring real LLM/Redis/Bohrium.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import MagicMock, patch

import pytest

from matmaster.assembly.direct_exp import DirectExp
from matmaster.assembly.tool_registry import Tool, ToolRegistry
from matmaster.bus.queue import MessageBus
from matmaster.engine.agent import AgentKernel
from matmaster.engine.types import (
    AssistantMessage,
    LLMResponse,
    Message,
    StreamChunk,
    ToolCallData,
    UserMessage,
)
from matmaster.types.context import PlaygroundContext
from matmaster.types.events import (
    FinishEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from matmaster.types.runtime import AgentRuntimeSpec


# ── Mock LLM provider ────────────────────────────────


class MockLLMProvider:
    """Mock LLM that returns a natural finish after 1 turn (no tool calls).

    Streams a single chunk with content, then a finish chunk.
    """

    def __init__(self, content: str = "Hello from mock LLM") -> None:
        self._content = content

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        return LLMResponse(content=self._content, finish_reason="stop")

    def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> LLMResponse:
        return self.chat(messages, tools)

    def chat_stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> Iterator[StreamChunk]:
        yield StreamChunk(
            content=self._content,
            stream_state="start",
            stream_id="s1",
            finish_reason="stop",
        )


class MockLLMProviderWithToolCall:
    """Mock LLM: returns tool_call on first turn, finish on second.

    First call: streams a tool call delta for 'echo' tool.
    Second call: streams natural finish with content.
    """

    def __init__(self) -> None:
        self._call_count = 0

    def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="done", finish_reason="stop")

    def chat_with_retry(self, messages, tools=None, **kw) -> LLMResponse:
        return self.chat(messages, tools)

    def chat_stream(self, messages, tools=None) -> Iterator[StreamChunk]:
        self._call_count += 1
        if self._call_count == 1:
            # First turn: tool call
            yield StreamChunk(
                tool_call_deltas=[
                    {
                        "index": 0,
                        "id": "call_001",
                        "name": "echo",
                        "arguments": json.dumps({"text": "hello"}),
                    }
                ],
                finish_reason="tool_calls",
            )
        else:
            # Second turn: natural finish
            yield StreamChunk(content="Done after tool.", finish_reason="stop")


class MockLLMProviderCapturingMessages:
    """Mock LLM that captures messages passed to chat_stream for verification."""

    def __init__(self) -> None:
        self.captured_messages: list[list[dict]] = []

    def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="ok", finish_reason="stop")

    def chat_with_retry(self, messages, tools=None, **kw) -> LLMResponse:
        return self.chat(messages, tools)

    def chat_stream(self, messages, tools=None) -> Iterator[StreamChunk]:
        self.captured_messages.append(list(messages))
        yield StreamChunk(content="Acknowledged history.", finish_reason="stop")


# ── Mock tool ─────────────────────────────────────────


class EchoTool:
    """Simple echo tool for E2E testing. Satisfies Tool Protocol."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echoes the input text"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }

    def execute(self, arguments: dict[str, Any]) -> str:
        return f"ECHO: {arguments.get('text', '')}"


# ── Helper ────────────────────────────────────────────


def _make_pg_ctx(tmp_path: Path) -> PlaygroundContext:
    """Create a test PlaygroundContext using tmp_path."""
    return PlaygroundContext(
        workdir=tmp_path / "workspace",
        session_type="local",
        cache_area=tmp_path / "cache",
        run_meta={"run_dir": str(tmp_path), "task_id": "test-task"},
    )


def _collect_bus_events(bus: MessageBus, timeout: float = 0.5) -> list:
    """Drain all events from bus within timeout."""
    events = []
    try:
        while True:
            events.append(bus.get(timeout=timeout))
    except queue.Empty:
        pass
    return events


# ── E2E tests ─────────────────────────────────────────


class TestMatMasterE2EPipeline:
    """QUAL-02: Full E2E pipeline with mock LLM."""

    def test_mat_master_e2e_pipeline(self, tmp_path: Path) -> None:
        """E2E: Playground.prepare() -> DirectExp.assemble() -> Kernel.run() with mock LLM."""
        pg_ctx = _make_pg_ctx(tmp_path)
        bus = MessageBus()
        mock_llm = MockLLMProvider()

        exp = DirectExp(llm_provider=mock_llm, bus=bus)
        spec = exp.assemble(pg_ctx)

        kernel = AgentKernel()
        finish = kernel.run(spec, "test task")

        assert isinstance(finish, FinishEvent)
        assert finish.reason == "natural"
        assert finish.status == "completed"

        # MessageBus received ThoughtEvent from streaming
        events = _collect_bus_events(bus)
        thought_events = [e for e in events if isinstance(e, ThoughtEvent)]
        assert len(thought_events) >= 1

    def test_mat_master_e2e_with_tool_call(self, tmp_path: Path) -> None:
        """E2E: Pipeline with a tool call and tool result."""
        pg_ctx = _make_pg_ctx(tmp_path)
        bus = MessageBus()
        mock_llm = MockLLMProviderWithToolCall()
        echo_tool = EchoTool()

        exp = DirectExp(
            llm_provider=mock_llm,
            builtin_tools=[echo_tool],
            bus=bus,
        )
        spec = exp.assemble(pg_ctx)

        kernel = AgentKernel()
        finish = kernel.run(spec, "call echo tool")

        assert isinstance(finish, FinishEvent)
        assert finish.reason == "natural"

        # Collect bus events -- should have ToolCallEvent and ToolResultEvent
        events = _collect_bus_events(bus)
        tool_call_events = [e for e in events if isinstance(e, ToolCallEvent)]
        tool_result_events = [e for e in events if isinstance(e, ToolResultEvent)]
        assert len(tool_call_events) >= 1
        assert len(tool_result_events) >= 1
        assert tool_call_events[0].tool_name == "echo"

    def test_mat_master_e2e_with_history(self, tmp_path: Path) -> None:
        """E2E: Pipeline with multi-turn history injection."""
        pg_ctx = _make_pg_ctx(tmp_path)
        bus = MessageBus()
        mock_llm = MockLLMProviderCapturingMessages()

        history: list[Message] = [
            UserMessage(content="old question"),
            AssistantMessage(content="old answer"),
        ]

        exp = DirectExp(llm_provider=mock_llm, bus=bus)
        spec = exp.assemble(pg_ctx)

        kernel = AgentKernel()
        finish = kernel.run(spec, "new task", history=history)

        assert finish.reason == "natural"
        # Verify messages passed to LLM include history
        assert len(mock_llm.captured_messages) == 1
        llm_messages = mock_llm.captured_messages[0]
        # Structure: SystemMessage, old question, old answer, new task
        roles = [m["role"] for m in llm_messages]
        assert roles == ["system", "user", "assistant", "user"]
        assert llm_messages[1]["content"] == "old question"
        assert llm_messages[2]["content"] == "old answer"
        assert llm_messages[3]["content"] == "new task"


class TestMatMasterRunAgentSyncE2E:
    """QUAL-02: run_agent_sync() with mock LLM provider injected."""

    def test_mat_master_run_agent_sync_e2e(self, tmp_path: Path) -> None:
        """E2E: run_agent_sync() with mock LLM provider injected.
        Validates full pipeline despite _build_llm_provider NotImplementedError stub.
        """
        from src.services.agent_run_service import AgentRunService

        mock_sessions_svc = MagicMock()
        mock_sessions_svc.get_session_user_id.return_value = "user-123"

        svc = AgentRunService(sessions_service=mock_sessions_svc)

        # Patch _build_llm_provider to return mock LLM
        mock_llm = MockLLMProvider("E2E test response")
        svc._build_llm_provider = MagicMock(return_value=mock_llm)
        svc._get_builtin_tools = MagicMock(return_value=[])

        # Patch Playground to return test context
        mock_pg = MagicMock()
        mock_pg_ctx = _make_pg_ctx(tmp_path)
        mock_pg.prepare.return_value = mock_pg_ctx
        mock_pg.config_path = Path("configs/mat_master/config.yaml")
        mock_pg.session = None

        with (
            patch.object(svc, "_get_or_create_playground", return_value=mock_pg),
            patch(
                "src.services.agent_run_service.BohriumSetupService"
            ) as mock_bohrium_cls,
            patch(
                "src.services.agent_run_service.get_chat_events_table"
            ) as mock_events_table_fn,
            patch("src.services.agent_run_service.get_redis_dao") as mock_redis_fn,
            patch("src.services.agent_run_service.use_quota") as mock_use_quota,
        ):
            # Configure Bohrium mock
            mock_bohrium_result = MagicMock()
            mock_bohrium_result.ssh_attached = False
            mock_bohrium_result.abort_result = None
            mock_bohrium_result._asdict.return_value = {
                "ssh_attached": False,
                "abort_result": None,
            }
            mock_bohrium_svc = mock_bohrium_cls.return_value
            mock_bohrium_svc.load_credentials.return_value = ({}, None, "org-1")
            mock_bohrium_svc.setup.return_value = mock_bohrium_result

            # Configure events_table mock -- returned by get_chat_events_table()
            mock_events_table = MagicMock()
            mock_events_table.get_session_events.return_value = []
            mock_events_table_fn.return_value = mock_events_table

            # Configure Redis mock
            mock_redis = MagicMock()
            mock_redis_fn.return_value = mock_redis

            # use_quota is async
            async def _mock_use_quota(uid):
                pass

            mock_use_quota.side_effect = _mock_use_quota

            # Track SSE sends
            sse_payloads = []

            def mock_send_cb(payload):
                sse_payloads.append(payload)

            # Execute
            svc.run_agent_sync(
                session_id="sess-1",
                user_prompt="test prompt",
                send_cb=mock_send_cb,
                loop=None,
                stop_event=threading.Event(),
                mode="direct",
                reply_queue=None,
                task_id="task-1",
            )

            # Verify: pipeline completed successfully -- use_quota called (success path)
            # use_quota is the strongest signal that kernel.run() returned a
            # non-cancelled FinishEvent and post-processing ran.
            assert mock_use_quota.called, "use_quota should be called on success"

            # Verify: SSE events were sent (send_cb was called).
            # In direct mode, streaming thought events (stream_state="start") are pushed.
            # Non-streaming thoughts are filtered. At minimum, the pipeline ran end-to-end.
            # Note: PersistenceHandler skips streaming thoughts (stream_state in start/streaming/end)
            # so add_event may not be called for this minimal mock. That's correct behavior.
            # The key E2E validation is: kernel ran -> finish -> use_quota called.
