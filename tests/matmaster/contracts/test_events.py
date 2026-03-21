"""Tests for AgentEvent, SystemEvent, and BusEvent discriminated unions."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from matmaster.contracts.events import (
    AgentEvent,
    AssistantStateEvent,
    BohriumNodeEvent,
    BusEvent,
    CancelledEvent,
    ConfirmationRequestEvent,
    ConfirmationTimeoutEvent,
    ContextCompactionEvent,
    ErrorEvent,
    ExpRunEvent,
    FinishEvent,
    McpConnectEvent,
    McpServerStatusEvent,
    SkillHitEvent,
    SystemEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
    WorkspaceUploadErrorEvent,
)


# ── Individual AgentEvent types ─────────────────────────


class TestThoughtEvent:
    def test_instantiation(self) -> None:
        evt = ThoughtEvent(source="agent", content="hello")
        assert evt.type == "thought"
        assert evt.source == "agent"
        assert evt.content == "hello"
        assert isinstance(evt.timestamp, datetime)

    def test_defaults(self) -> None:
        evt = ThoughtEvent(source="agent")
        assert evt.content == ""
        assert evt.stream_state is None
        assert evt.stream_id is None
        assert evt.token_count == 0
        assert evt.context is None
        assert evt.reasoning_content is None

    def test_streaming(self) -> None:
        evt = ThoughtEvent(
            source="agent", stream_state="start", stream_id="s1"
        )
        assert evt.stream_state == "start"
        assert evt.stream_id == "s1"


class TestToolCallEvent:
    def test_instantiation(self) -> None:
        evt = ToolCallEvent(
            source="agent",
            call_id="c1",
            tool_name="bash",
            arguments={"cmd": "ls"},
        )
        assert evt.type == "tool_call"
        assert evt.call_id == "c1"
        assert evt.tool_name == "bash"
        assert evt.arguments == {"cmd": "ls"}


class TestToolResultEvent:
    def test_instantiation(self) -> None:
        evt = ToolResultEvent(
            source="agent",
            call_id="c1",
            tool_name="bash",
            result="output",
        )
        assert evt.type == "tool_result"
        assert evt.result == "output"
        assert evt.info == {}


class TestFinishEvent:
    def test_defaults(self) -> None:
        evt = FinishEvent(source="agent")
        assert evt.type == "finish"
        assert evt.status == "completed"
        assert evt.reason == ""
        assert evt.final_content is None


class TestErrorEvent:
    def test_instantiation(self) -> None:
        evt = ErrorEvent(source="agent", message="fail")
        assert evt.type == "error"
        assert evt.message == "fail"
        assert evt.traceback is None


class TestAssistantStateEvent:
    def test_instantiation(self) -> None:
        evt = AssistantStateEvent(source="agent", state={"content": "hi"})
        assert evt.type == "assistant_state"
        assert evt.state == {"content": "hi"}


class TestSkillHitEvent:
    def test_instantiation(self) -> None:
        evt = SkillHitEvent(source="agent", skill_name="research")
        assert evt.type == "skill_hit"
        assert evt.skill_name == "research"


# ── Individual SystemEvent types ────────────────────────


class TestSystemEvents:
    def test_confirmation_request(self) -> None:
        evt = ConfirmationRequestEvent(
            source="system", question="proceed?", mode="timeout"
        )
        assert evt.type == "confirmation_request"
        assert evt.question == "proceed?"
        assert evt.mode == "timeout"
        assert evt.timeout_seconds is None
        assert evt.context is None
        assert evt.actions == []
        assert evt.origin is None

    def test_confirmation_timeout(self) -> None:
        evt = ConfirmationTimeoutEvent(
            source="system", question="proceed?"
        )
        assert evt.type == "confirmation_timeout"
        assert evt.default_reply is None

    def test_context_compaction(self) -> None:
        evt = ContextCompactionEvent(
            source="system", payload={"tokens_before": 100000}
        )
        assert evt.type == "context_compaction"

    def test_exp_run(self) -> None:
        evt = ExpRunEvent(source="system", exp_name="mat_master")
        assert evt.type == "exp_run"
        assert evt.exp_name == "mat_master"

    def test_cancelled(self) -> None:
        evt = CancelledEvent(source="system")
        assert evt.type == "cancelled"
        assert evt.reason == ""

    def test_workspace_upload_error(self) -> None:
        evt = WorkspaceUploadErrorEvent(source="system", message="upload failed")
        assert evt.type == "workspace_upload_error"

    def test_bohrium_node(self) -> None:
        evt = BohriumNodeEvent(source="system")
        assert evt.type == "bohrium_node"
        assert evt.payload == {}

    def test_mcp_server_status(self) -> None:
        evt = McpServerStatusEvent(source="system", server_name="code-server")
        assert evt.type == "mcp_server_status"
        assert evt.transport is None
        assert evt.phase == ""
        assert evt.detail == {}

    def test_mcp_connect(self) -> None:
        evt = McpConnectEvent(source="system")
        assert evt.type == "mcp_connect"
        assert evt.phase == ""
        assert evt.message == ""
        assert evt.elapsed_ms is None
        assert evt.error is None


# ── Discriminated union tests ───────────────────────────


_agent_event_adapter = TypeAdapter(AgentEvent)
_system_event_adapter = TypeAdapter(SystemEvent)
_bus_event_adapter = TypeAdapter(BusEvent)


class TestAgentEventDiscriminator:
    def test_thought(self) -> None:
        result = _agent_event_adapter.validate_python(
            {"type": "thought", "source": "a", "content": "x"}
        )
        assert isinstance(result, ThoughtEvent)

    def test_all_agent_types(self) -> None:
        payloads = [
            {"type": "thought", "source": "a"},
            {"type": "tool_call", "source": "a", "call_id": "c", "tool_name": "t", "arguments": {}},
            {"type": "tool_result", "source": "a", "call_id": "c", "tool_name": "t", "result": "r"},
            {"type": "finish", "source": "a"},
            {"type": "error", "source": "a", "message": "m"},
            {"type": "assistant_state", "source": "a", "state": {}},
            {"type": "skill_hit", "source": "a", "skill_name": "s"},
        ]
        expected_types = [
            ThoughtEvent, ToolCallEvent, ToolResultEvent, FinishEvent,
            ErrorEvent, AssistantStateEvent, SkillHitEvent,
        ]
        for payload, expected in zip(payloads, expected_types):
            result = _agent_event_adapter.validate_python(payload)
            assert isinstance(result, expected), f"Expected {expected.__name__}, got {type(result).__name__}"


class TestSystemEventDiscriminator:
    def test_confirmation_request(self) -> None:
        result = _system_event_adapter.validate_python(
            {"type": "confirmation_request", "source": "s", "question": "q", "mode": "m"}
        )
        assert isinstance(result, ConfirmationRequestEvent)

    def test_all_system_types(self) -> None:
        payloads = [
            {"type": "confirmation_request", "source": "s", "question": "q", "mode": "m"},
            {"type": "confirmation_timeout", "source": "s", "question": "q"},
            {"type": "context_compaction", "source": "s", "payload": {}},
            {"type": "exp_run", "source": "s", "exp_name": "e"},
            {"type": "cancelled", "source": "s"},
            {"type": "workspace_upload_error", "source": "s", "message": "m"},
            {"type": "bohrium_node", "source": "s"},
            {"type": "mcp_server_status", "source": "s", "server_name": "n"},
            {"type": "mcp_connect", "source": "s"},
        ]
        expected_types = [
            ConfirmationRequestEvent, ConfirmationTimeoutEvent,
            ContextCompactionEvent, ExpRunEvent, CancelledEvent,
            WorkspaceUploadErrorEvent, BohriumNodeEvent,
            McpServerStatusEvent, McpConnectEvent,
        ]
        for payload, expected in zip(payloads, expected_types):
            result = _system_event_adapter.validate_python(payload)
            assert isinstance(result, expected), f"Expected {expected.__name__}, got {type(result).__name__}"


class TestBusEventUnion:
    def test_validates_all_16_types(self) -> None:
        """BusEvent union can validate all 16 event types."""
        payloads = [
            # 7 AgentEvent types
            {"type": "thought", "source": "a"},
            {"type": "tool_call", "source": "a", "call_id": "c", "tool_name": "t", "arguments": {}},
            {"type": "tool_result", "source": "a", "call_id": "c", "tool_name": "t", "result": "r"},
            {"type": "finish", "source": "a"},
            {"type": "error", "source": "a", "message": "m"},
            {"type": "assistant_state", "source": "a", "state": {}},
            {"type": "skill_hit", "source": "a", "skill_name": "s"},
            # 9 SystemEvent types
            {"type": "confirmation_request", "source": "s", "question": "q", "mode": "m"},
            {"type": "confirmation_timeout", "source": "s", "question": "q"},
            {"type": "context_compaction", "source": "s", "payload": {}},
            {"type": "exp_run", "source": "s", "exp_name": "e"},
            {"type": "cancelled", "source": "s"},
            {"type": "workspace_upload_error", "source": "s", "message": "m"},
            {"type": "bohrium_node", "source": "s"},
            {"type": "mcp_server_status", "source": "s", "server_name": "n"},
            {"type": "mcp_connect", "source": "s"},
        ]
        for payload in payloads:
            result = _bus_event_adapter.validate_python(payload)
            assert result.type == payload["type"]

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            _bus_event_adapter.validate_python(
                {"type": "nonexistent", "source": "x"}
            )


class TestEventSerializationRoundtrip:
    def test_roundtrip_all_types(self) -> None:
        """For each event type, model_dump -> validate_python returns same class."""
        events = [
            ThoughtEvent(source="a", content="hello"),
            ToolCallEvent(source="a", call_id="c", tool_name="t", arguments={}),
            ToolResultEvent(source="a", call_id="c", tool_name="t", result="r"),
            FinishEvent(source="a"),
            ErrorEvent(source="a", message="m"),
            AssistantStateEvent(source="a", state={"k": "v"}),
            SkillHitEvent(source="a", skill_name="s"),
            ConfirmationRequestEvent(source="s", question="q", mode="m"),
            ConfirmationTimeoutEvent(source="s", question="q"),
            ContextCompactionEvent(source="s", payload={}),
            ExpRunEvent(source="s", exp_name="e"),
            CancelledEvent(source="s"),
            WorkspaceUploadErrorEvent(source="s", message="m"),
            BohriumNodeEvent(source="s"),
            McpServerStatusEvent(source="s", server_name="n"),
            McpConnectEvent(source="s"),
        ]
        for event in events:
            data = event.model_dump()
            restored = _bus_event_adapter.validate_python(data)
            assert type(restored) is type(event), (
                f"Roundtrip failed: {type(event).__name__} -> {type(restored).__name__}"
            )


class TestNoTypeCollision:
    def test_all_16_type_literals_are_unique(self) -> None:
        """All 16 type literals must be globally unique strings."""
        type_values = [
            "thought", "tool_call", "tool_result", "finish", "error",
            "assistant_state", "skill_hit",
            "confirmation_request", "confirmation_timeout",
            "context_compaction", "exp_run", "cancelled",
            "workspace_upload_error", "bohrium_node",
            "mcp_server_status", "mcp_connect",
        ]
        assert len(type_values) == 16
        assert len(set(type_values)) == 16
