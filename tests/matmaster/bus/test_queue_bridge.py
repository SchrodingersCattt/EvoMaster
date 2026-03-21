"""Tests for QueueBridge SSE payload adapter.

Tests all 16 BusEvent type conversions to SSE payload dict format.
"""

import queue

from matmaster.bus.bridge import QueueBridge
from matmaster.bus.queue import MessageBus
from matmaster.contracts.events import (
    AssistantStateEvent,
    BohriumNodeEvent,
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
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
    WorkspaceUploadErrorEvent,
)


def _create_bridge() -> tuple[MessageBus, QueueBridge]:
    bus = MessageBus()
    bridge = QueueBridge(bus)
    return bus, bridge


# ── ThoughtEvent tests ─────────────────────────────────


class TestThoughtEventConversion:
    def test_thought_event_basic(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(ThoughtEvent(source="agent", content="hello"))
        payload = bridge.next_payload()
        assert payload == {"source": "agent", "type": "thought", "content": "hello"}

    def test_thought_event_streaming(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(
            ThoughtEvent(
                source="agent", content="", stream_state="start", stream_id="s1"
            )
        )
        payload = bridge.next_payload()
        assert payload["stream_state"] == "start"
        assert payload["stream_id"] == "s1"

    def test_thought_event_token_count(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(ThoughtEvent(source="agent", content="", token_count=50))
        payload = bridge.next_payload()
        assert payload["token_count"] == 50

    def test_thought_event_context(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(ThoughtEvent(source="agent", content="", context="step_execution"))
        payload = bridge.next_payload()
        assert payload["context"] == "step_execution"

    def test_thought_event_omits_none_fields(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(ThoughtEvent(source="agent", content="hi"))
        payload = bridge.next_payload()
        assert "stream_state" not in payload
        assert "stream_id" not in payload
        assert "token_count" not in payload
        assert "context" not in payload


# ── ToolCallEvent / ToolResultEvent tests ──────────────


class TestToolEventConversion:
    def test_tool_call_event(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(
            ToolCallEvent(
                source="M",
                call_id="c1",
                tool_name="bash",
                arguments={"cmd": "ls"},
            )
        )
        payload = bridge.next_payload()
        assert payload["source"] == "M"
        assert payload["type"] == "tool_call"
        assert payload["content"] == {
            "id": "c1",
            "name": "bash",
            "args": {"cmd": "ls"},
        }

    def test_tool_result_event(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(
            ToolResultEvent(
                source="M",
                call_id="c1",
                tool_name="bash",
                result="ok",
                info={"time": 1},
            )
        )
        payload = bridge.next_payload()
        assert payload["source"] == "M"
        assert payload["type"] == "tool_result"
        assert payload["content"] == {
            "id": "c1",
            "name": "bash",
            "result": "ok",
            "info": {"time": 1},
        }


# ── FinishEvent tests ──────────────────────────────────


class TestFinishEventConversion:
    def test_finish_event_with_final_content(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(FinishEvent(source="a", final_content="done"))
        payload = bridge.next_payload()
        assert payload["content"] == "done"

    def test_finish_event_without_final_content(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(FinishEvent(source="a", status="completed", final_content=None))
        payload = bridge.next_payload()
        assert payload["content"] == "completed"


# ── ErrorEvent test ────────────────────────────────────


class TestErrorEventConversion:
    def test_error_event(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(ErrorEvent(source="a", message="fail"))
        payload = bridge.next_payload()
        assert payload["content"] == "fail"


# ── AssistantStateEvent test ───────────────────────────


class TestAssistantStateEventConversion:
    def test_assistant_state_event(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(AssistantStateEvent(source="a", state={"k": "v"}))
        payload = bridge.next_payload()
        assert payload["content"] == {"k": "v"}


# ── SkillHitEvent test ─────────────────────────────────


class TestSkillHitEventConversion:
    def test_skill_hit_event(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(SkillHitEvent(source="a", skill_name="research"))
        payload = bridge.next_payload()
        assert payload["content"] == "research"


# ── ConfirmationRequestEvent tests ─────────────────────


class TestConfirmationRequestEventConversion:
    def test_confirmation_request_event_full(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(
            ConfirmationRequestEvent(
                source="s",
                question="continue?",
                mode="timeout",
                timeout_seconds=30,
                context="tool_call",
                actions=["yes", "no"],
                origin="agent",
            )
        )
        payload = bridge.next_payload()
        assert payload["type"] == "confirmation_request"
        content = payload["content"]
        assert content["question"] == "continue?"
        assert content["mode"] == "timeout"
        assert content["timeout_seconds"] == 30
        assert content["context"] == "tool_call"
        assert content["actions"] == ["yes", "no"]
        assert content["origin"] == "agent"

    def test_confirmation_request_event_minimal(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(
            ConfirmationRequestEvent(
                source="s",
                question="continue?",
                mode="block",
            )
        )
        payload = bridge.next_payload()
        content = payload["content"]
        assert content["question"] == "continue?"
        assert content["mode"] == "block"
        assert "timeout_seconds" not in content
        assert "context" not in content
        assert "actions" not in content
        assert "origin" not in content


# ── ConfirmationTimeoutEvent test ──────────────────────


class TestConfirmationTimeoutEventConversion:
    def test_confirmation_timeout_event(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(
            ConfirmationTimeoutEvent(
                source="s", question="continue?", default_reply="yes"
            )
        )
        payload = bridge.next_payload()
        assert payload["content"] == {
            "question": "continue?",
            "default_reply": "yes",
        }


# ── ContextCompactionEvent test ────────────────────────


class TestContextCompactionEventConversion:
    def test_context_compaction_event(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(
            ContextCompactionEvent(
                source="System", payload={"before": 100, "after": 50}
            )
        )
        payload = bridge.next_payload()
        assert payload["content"] == {"before": 100, "after": 50}


# ── ExpRunEvent test ───────────────────────────────────


class TestExpRunEventConversion:
    def test_exp_run_event(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(ExpRunEvent(source="M", exp_name="phonon_calc"))
        payload = bridge.next_payload()
        assert payload["content"] == "phonon_calc"


# ── CancelledEvent tests ──────────────────────────────


class TestCancelledEventConversion:
    def test_cancelled_event_with_reason(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(CancelledEvent(source="System", reason="user request"))
        payload = bridge.next_payload()
        assert payload["content"] == "user request"

    def test_cancelled_event_default(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(CancelledEvent(source="System", reason=""))
        payload = bridge.next_payload()
        assert payload["content"] == "Task cancelled by user."


# ── WorkspaceUploadErrorEvent test ─────────────────────


class TestWorkspaceUploadErrorEventConversion:
    def test_workspace_upload_error_event(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(WorkspaceUploadErrorEvent(source="System", message="disk full"))
        payload = bridge.next_payload()
        assert payload["content"] == "disk full"


# ── BohriumNodeEvent test ──────────────────────────────


class TestBohriumNodeEventConversion:
    def test_bohrium_node_event(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(BohriumNodeEvent(source="System", payload={"node": "n1"}))
        payload = bridge.next_payload()
        assert payload["content"] == {"node": "n1"}


# ── McpServerStatusEvent test ──────────────────────────


class TestMcpServerStatusEventConversion:
    def test_mcp_server_status_event(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(
            McpServerStatusEvent(
                source="System",
                server_name="mcp1",
                transport="stdio",
                phase="start",
                detail={"progress": 50},
            )
        )
        payload = bridge.next_payload()
        assert payload["content"] == {"progress": 50}
        assert payload["mcp_phase"] == "start"
        assert payload["mcp_server"] == "mcp1"
        assert payload["mcp_transport"] == "stdio"


# ── McpConnectEvent tests ──────────────────────────────


class TestMcpConnectEventConversion:
    def test_mcp_connect_event_full(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(
            McpConnectEvent(
                source="System",
                phase="ready",
                message="connected",
                elapsed_ms=150,
                error="timeout warning",
            )
        )
        payload = bridge.next_payload()
        content = payload["content"]
        assert content["phase"] == "ready"
        assert content["message"] == "connected"
        assert content["elapsed_ms"] == 150
        assert content["error"] == "timeout warning"
        assert payload["mcp_phase"] == "ready"

    def test_mcp_connect_event_minimal(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(
            McpConnectEvent(source="System", phase="start", message="connecting")
        )
        payload = bridge.next_payload()
        content = payload["content"]
        assert content["phase"] == "start"
        assert content["message"] == "connecting"
        assert "elapsed_ms" not in content
        assert "error" not in content


# ── Integration / Timeout tests ────────────────────────


class TestQueueBridgeIntegration:
    def test_next_payload_timeout(self) -> None:
        bus, bridge = _create_bridge()
        try:
            bridge.next_payload(timeout=0.05)
            assert False, "Expected queue.Empty"
        except queue.Empty:
            pass

    def test_integration_fifo(self) -> None:
        bus, bridge = _create_bridge()
        bus.emit(ThoughtEvent(source="a", content="thought"))
        bus.emit(ErrorEvent(source="a", message="err"))
        bus.emit(SkillHitEvent(source="a", skill_name="calc"))

        p1 = bridge.next_payload()
        p2 = bridge.next_payload()
        p3 = bridge.next_payload()

        assert p1["type"] == "thought"
        assert p1["content"] == "thought"
        assert p2["type"] == "error"
        assert p2["content"] == "err"
        assert p3["type"] == "skill_hit"
        assert p3["content"] == "calc"
