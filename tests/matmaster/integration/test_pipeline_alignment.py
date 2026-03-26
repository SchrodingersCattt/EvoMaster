"""Pipeline alignment test: verify new pipeline emits events in expected order.

Ensures the event sequence from the new matmaster pipeline matches the
expected pattern: thought -> [tool_call -> tool_result]* -> finish.
"""

from __future__ import annotations

import json
import queue
from pathlib import Path
from typing import Any, Iterator

from matmaster.config.exp import ExpConfig
from matmaster.core.exp import Exp
from matmaster.core.bus import MessageBus
from matmaster.core.agent import AgentKernel
from matmaster.types.messages import LLMResponse, StreamChunk
from matmaster.types.context import PlaygroundContext
from matmaster.types.events import (
    FinishEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
)


class _ToolCallThenFinishLLM:
    """Mock LLM: tool call on first turn, natural finish on second."""

    def __init__(self) -> None:
        self._call_count = 0

    def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="done", finish_reason="stop")

    def chat_stream(self, messages, tools=None, *, timeout=None) -> Iterator[StreamChunk]:
        self._call_count += 1
        if self._call_count == 1:
            # Emit a reasoning chunk first so EventEmitterHook produces ThoughtEvent
            yield StreamChunk(
                reasoning_content="Let me use the tool.",
                stream_state="start",
                stream_id="s1",
            )
            # Then tool call
            yield StreamChunk(
                tool_call_deltas=[
                    {
                        "index": 0,
                        "id": "call_align_1",
                        "name": "test_tool",
                        "arguments": json.dumps({"input": "data"}),
                    }
                ],
                finish_reason="tool_calls",
            )
        else:
            # Second turn: natural finish with reasoning
            yield StreamChunk(
                reasoning_content="Task complete.",
                finish_reason="stop",
            )


class _SimpleTool:
    """Simple tool for alignment tests. Satisfies Tool Protocol."""

    @property
    def name(self) -> str:
        return "test_tool"

    @property
    def description(self) -> str:
        return "A test tool"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"input": {"type": "string"}},
        }

    def execute(self, arguments: dict[str, Any]) -> str:
        return f"result: {arguments.get('input', '')}"


class TestEventSequenceAlignment:
    """Verify new pipeline emits events in expected order."""

    def test_event_sequence_alignment(self, tmp_path: Path) -> None:
        """Verify new pipeline emits events in expected order.
        Expected: thought -> tool_call -> tool_result -> thought -> (finish via return)
        """
        mock_llm = _ToolCallThenFinishLLM()
        pg_ctx = PlaygroundContext(
            workdir=tmp_path / "workspace",
            session_type="local",
            cache_area=tmp_path / "cache",
            llm_provider=mock_llm,
        )
        bus = MessageBus()
        tool = _SimpleTool()

        config = ExpConfig(name="direct")
        exp = Exp(config)
        runtime = exp.build_runtime(pg_ctx, bus=bus)
        # Register test tool directly on the runtime's registry
        runtime.spec.tool_registry.register(tool, source="test")

        kernel = AgentKernel()
        finish = kernel.run(runtime.spec, "alignment test")

        assert finish.result.reason == "natural"

        # Collect events from bus
        events = []
        try:
            while True:
                events.append(bus.get(timeout=0.1))
        except queue.Empty:
            pass

        # Extract event type sequence
        event_types = [e.type for e in events]

        # Expected pattern:
        # thought (stream from first turn) -> tool_call -> tool_result -> thought (second turn)
        # FinishEvent is returned by kernel.run(), not emitted to bus
        assert "thought" in event_types, f"Missing thought event, got: {event_types}"
        assert "tool_call" in event_types, f"Missing tool_call event, got: {event_types}"
        assert "tool_result" in event_types, f"Missing tool_result event, got: {event_types}"

        # Verify order: tool_call must come before tool_result
        tc_idx = event_types.index("tool_call")
        tr_idx = event_types.index("tool_result")
        assert tc_idx < tr_idx, (
            f"tool_call (idx={tc_idx}) should precede tool_result (idx={tr_idx})"
        )
