"""Tests for AgentEvent, SystemEvent, and BusEvent discriminated unions."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from matmaster.types.events import (
    AgentEvent,
    AskQuestionEvent,
    AskQuestionReplyEvent,
    AskQuestionTimeoutEvent,
    AssistantStateEvent,
    BohriumNodeEvent,
    BusEvent,
    CancelledEvent,
    CompactionEvent,
    ErrorEvent,
    ExpRunEvent,
    FinishDetail,
    McpConnectEvent,
    McpServerStatusEvent,
    ResponseEvent,
    ResponseFiguresEvent,
    RunResultEvent,
    SkillHitEvent,
    StreamClosedEvent,
    SystemEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolProgressEvent,
    ToolResultEvent,
    WorkspaceUploadErrorEvent,
)
from matmaster.types.figures import FigureDescriptor

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
        evt = ThoughtEvent(source="agent", stream_state="start", stream_id="s1")
        assert evt.stream_state == "start"
        assert evt.stream_id == "s1"


class TestResponseEvent:
    def test_instantiation(self) -> None:
        evt = ResponseEvent(source="agent", content="hello")
        assert evt.type == "response"
        assert evt.source == "agent"
        assert evt.content == "hello"
        assert isinstance(evt.timestamp, datetime)

    def test_response_event_defaults(self) -> None:
        evt = ResponseEvent(source="agent")
        assert evt.type == "response"
        assert evt.content == ""
        assert evt.stream_state is None
        assert evt.stream_id is None


class TestResponseEventUsage:
    def test_response_usage_fields(self) -> None:
        evt = ResponseEvent(
            source="agent",
            content="answer",
            stream_state="complete",
            turn_index=2,
            turn_usage={"prompt_tokens": 10, "completion_tokens": 4},
            total_usage={"prompt_tokens": 30, "completion_tokens": 9},
            usage_vendor={"inputTokens": 10, "outputTokens": 4},
        )

        assert evt.turn_index == 2
        assert evt.turn_usage == {"prompt_tokens": 10, "completion_tokens": 4}
        assert evt.total_usage == {"prompt_tokens": 30, "completion_tokens": 9}
        assert evt.usage_vendor == {"inputTokens": 10, "outputTokens": 4}

    def test_response_usage_defaults(self) -> None:
        evt = ResponseEvent(source="agent")
        assert evt.turn_index is None
        assert evt.turn_usage == {}
        assert evt.total_usage == {}
        assert evt.usage_vendor is None


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

    def test_spawn_id_defaults_to_none(self) -> None:
        evt = ToolCallEvent(
            source="agent",
            call_id="c1",
            tool_name="bash",
            arguments={"cmd": "ls"},
        )
        assert evt.spawn_id is None


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
        assert evt.status == "success"
        assert evt.payload == {}


def test_tool_result_turn_index_defaults_to_none() -> None:
    evt = ToolResultEvent(
        source="agent",
        call_id="c1",
        tool_name="bash",
        result="output",
    )
    assert evt.turn_index is None


class TestFinishDetail:
    def test_finish_detail_serializes_structured_fields(self) -> None:
        detail = FinishDetail(
            kind="output_length_exceeded",
            provider_finish_reason="length",
            message="Model output was truncated by the provider output-token limit.",
            content_chars=12,
            reasoning_chars=34,
            has_visible_content=True,
            has_reasoning=True,
            last_turn_usage={"completion_tokens": 4096},
            last_turn_usage_vendor={"outputTokens": 4096},
            truncation_risk=True,
        )

        dumped = detail.model_dump(mode="json")
        assert dumped["kind"] == "output_length_exceeded"
        assert dumped["last_turn_usage"]["completion_tokens"] == 4096
        assert dumped["last_turn_usage_vendor"]["outputTokens"] == 4096
        assert dumped["truncation_risk"] is True

    def test_finish_detail_rejects_unknown_kind(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            FinishDetail(kind="typo", message="bad")

        assert any(err["loc"] == ("kind",) for err in exc_info.value.errors())


class TestRunResultEvent:
    def test_defaults(self) -> None:
        evt = RunResultEvent(source="agent")
        assert evt.type == "run_result"
        assert evt.status == "completed"
        assert evt.reason == ""
        assert evt.final_content is None

    def test_rejects_legacy_finish_type(self) -> None:
        legacy_type = "fin" + "ish"
        with pytest.raises(ValidationError):
            RunResultEvent.model_validate({"type": legacy_type, "source": "agent"})

    def test_finish_detail_round_trips(self) -> None:
        evt = RunResultEvent(
            source="agent",
            status="failed",
            reason="invalid_finish",
            finish_detail=FinishDetail(
                kind="empty_response",
                message="Model stopped without a visible final answer.",
            ),
        )

        dumped = evt.model_dump(mode="json")
        assert dumped["finish_detail"]["kind"] == "empty_response"
        restored = _bus_event_adapter.validate_python(dumped)
        assert isinstance(restored, RunResultEvent)
        assert restored.finish_detail is not None
        assert restored.finish_detail.kind == "empty_response"


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

    def test_finish_detail_round_trips(self) -> None:
        evt = AssistantStateEvent(
            source="agent",
            state={"role": "assistant", "tool_calls": []},
            finish_detail=FinishDetail(
                kind="output_length_exceeded",
                provider_finish_reason="length",
                message="Model output was truncated by the provider output-token limit.",
                has_tool_calls=True,
                truncation_risk=True,
            ),
        )

        dumped = evt.model_dump(mode="json")
        assert dumped["finish_detail"]["has_tool_calls"] is True
        restored = _bus_event_adapter.validate_python(dumped)
        assert isinstance(restored, AssistantStateEvent)
        assert restored.finish_detail is not None
        assert restored.finish_detail.truncation_risk is True


def test_assistant_state_turn_index_defaults_to_none() -> None:
    evt = AssistantStateEvent(source="agent", state={"content": None})
    assert evt.turn_index is None


class TestSkillHitEvent:
    def test_instantiation(self) -> None:
        evt = SkillHitEvent(source="agent", skill_name="research")
        assert evt.type == "skill_hit"
        assert evt.skill_name == "research"


class TestToolProgressEvent:
    def test_instantiation(self) -> None:
        evt = ToolProgressEvent(
            source="agent",
            call_id="c1",
            tool_name="Bash",
            content="line 1",
        )
        assert evt.type == "tool_progress"
        assert evt.call_id == "c1"
        assert evt.tool_name == "Bash"
        assert evt.content == "line 1"


# ── Individual SystemEvent types ────────────────────────


class TestSystemEvents:
    def test_compaction(self) -> None:
        evt = CompactionEvent(
            source="context_compactor",
            compaction_id="task-1:root:1",
            status="running",
            phase="runtime",
            trigger_tokens=950,
        )
        assert evt.type == "compaction"

    def test_compaction_event_running_round_trip(self) -> None:
        evt = CompactionEvent(
            source="context_compactor",
            compaction_id="task-1:root:1",
            status="running",
            phase="runtime",
            trigger_tokens=950,
        )

        dumped = evt.model_dump(mode="json")
        restored = CompactionEvent.model_validate(dumped)

        assert restored.type == "compaction"
        assert restored.compaction_id == "task-1:root:1"
        assert restored.status == "running"
        assert restored.phase == "runtime"
        assert restored.strategy is None

    def test_compaction_event_complete_round_trip(self) -> None:
        evt = CompactionEvent(
            source="context_compactor",
            compaction_id="task-1:root:2",
            status="complete",
            phase="runtime",
            strategy="summary",
            durability="durable",
            trigger_tokens=1200,
            retained_turns=3,
            checkpoint_written=True,
            covered_until_event_id=88,
        )

        dumped = evt.model_dump(mode="json")
        restored = CompactionEvent.model_validate(dumped)

        assert restored.type == "compaction"
        assert restored.status == "complete"
        assert restored.strategy == "summary"
        assert restored.checkpoint_written is True
        assert restored.covered_until_event_id == 88

    def test_compaction_event_usage_fields_default_to_none(self) -> None:
        evt = CompactionEvent(
            source="context_compactor",
            compaction_id="root:1",
            status="complete",
            phase="runtime",
        )

        assert evt.turn_usage is None
        assert evt.total_usage is None

    def test_compaction_event_accepts_usage_fields(self) -> None:
        evt = CompactionEvent(
            source="context_compactor",
            compaction_id="root:1",
            status="complete",
            phase="runtime",
            turn_usage={"prompt_tokens": 40},
            total_usage={"prompt_tokens": 55},
        )

        assert evt.turn_usage == {"prompt_tokens": 40}
        assert evt.total_usage == {"prompt_tokens": 55}

    def test_exp_run(self) -> None:
        evt = ExpRunEvent(source="system", exp_name="mat_master")
        assert evt.type == "exp_run"
        assert evt.exp_name == "mat_master"

    def test_cancelled(self) -> None:
        evt = CancelledEvent(source="system")
        assert evt.type == "cancelled"
        assert evt.reason == ""

    def test_stream_closed(self) -> None:
        evt = StreamClosedEvent(source="system")
        assert evt.type == "stream_closed"
        assert evt.content == ""
        assert evt.task_completed is False
        assert evt.end_reason is None
        assert evt.treat_as_failure is None

    def test_stream_closed_rejects_legacy_end_type(self) -> None:
        legacy_type = "e" + "nd"
        with pytest.raises(ValidationError):
            StreamClosedEvent.model_validate({"type": legacy_type, "source": "system"})

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

    def test_response_figures(self) -> None:
        evt = ResponseFiguresEvent(
            source="system",
            figures=[
                FigureDescriptor(
                    figure_id="band_structure",
                    asset_url="https://oss.example/band.png",
                    caption="Si 的能带图",
                )
            ],
        )
        assert evt.type == "response_figures"
        assert evt.figures[0].figure_id == "band_structure"

    def test_ask_question(self) -> None:
        evt = AskQuestionEvent(
            source="system",
            request_id="aq_1",
            questions=[
                {
                    "question": "Which library should we use?",
                    "header": "Library",
                    "options": [
                        {
                            "label": "Pydantic (Recommended)",
                            "description": "Runtime validation",
                        },
                        {
                            "label": "dataclasses",
                            "description": "Stdlib only",
                        },
                    ],
                }
            ],
            preview_format="markdown",
        )
        assert evt.type == "ask_question"
        assert evt.request_id == "aq_1"
        assert evt.preview_format == "markdown"

    def test_ask_question_reply(self) -> None:
        evt = AskQuestionReplyEvent(
            source="user",
            request_id="aq_1",
            answers={"Which library should we use?": "Pydantic (Recommended)"},
        )
        assert evt.type == "ask_question_reply"

    def test_ask_question_timeout(self) -> None:
        evt = AskQuestionTimeoutEvent(
            source="system",
            request_id="aq_1",
            questions=[],
            reason="timeout",
        )
        assert evt.type == "ask_question_timeout"


# ── Discriminated union tests ───────────────────────────


_agent_event_adapter = TypeAdapter(AgentEvent)
_system_event_adapter = TypeAdapter(SystemEvent)
_bus_event_adapter = TypeAdapter(BusEvent)


def test_finish_detail_exported_from_types_package() -> None:
    import matmaster.types as types_pkg

    assert types_pkg.FinishDetail is FinishDetail


class TestAgentEventDiscriminator:
    def test_thought(self) -> None:
        result = _agent_event_adapter.validate_python(
            {"type": "thought", "source": "a", "content": "x"}
        )
        assert isinstance(result, ThoughtEvent)

    def test_all_agent_types(self) -> None:
        payloads = [
            {"type": "thought", "source": "a"},
            {"type": "response", "source": "a", "content": "hello"},
            {
                "type": "tool_call",
                "source": "a",
                "call_id": "c",
                "tool_name": "t",
                "arguments": {},
            },
            {
                "type": "tool_result",
                "source": "a",
                "call_id": "c",
                "tool_name": "t",
                "result": "r",
            },
            {"type": "run_result", "source": "a"},
            {"type": "error", "source": "a", "message": "m"},
            {"type": "assistant_state", "source": "a", "state": {}},
            {"type": "skill_hit", "source": "a", "skill_name": "s"},
            {
                "type": "tool_progress",
                "source": "a",
                "call_id": "c",
                "tool_name": "t",
            },
        ]
        expected_types = [
            ThoughtEvent,
            ResponseEvent,
            ToolCallEvent,
            ToolResultEvent,
            RunResultEvent,
            ErrorEvent,
            AssistantStateEvent,
            SkillHitEvent,
            ToolProgressEvent,
        ]
        for payload, expected in zip(payloads, expected_types):
            result = _agent_event_adapter.validate_python(payload)
            assert isinstance(
                result, expected
            ), f"Expected {expected.__name__}, got {type(result).__name__}"


class TestSharedSpawnIdField:
    def test_bus_event_round_trips_spawn_id(self) -> None:
        result = _bus_event_adapter.validate_python(
            {
                "type": "tool_call",
                "source": "a",
                "call_id": "c",
                "tool_name": "t",
                "arguments": {},
                "spawn_id": "deadbeefcafebabe",
            }
        )

        assert isinstance(result, ToolCallEvent)
        assert result.spawn_id == "deadbeefcafebabe"


class TestSystemEventDiscriminator:
    def test_all_system_types(self) -> None:
        payloads = [
            {
                "type": "ask_question",
                "source": "s",
                "request_id": "aq_1",
                "questions": [],
            },
            {
                "type": "ask_question_reply",
                "source": "s",
                "request_id": "aq_1",
                "answers": {},
            },
            {
                "type": "ask_question_timeout",
                "source": "s",
                "request_id": "aq_1",
                "questions": [],
            },
            {
                "type": "compaction",
                "source": "context_compactor",
                "compaction_id": "task-1:root:1",
                "status": "running",
                "phase": "runtime",
            },
            {"type": "exp_run", "source": "s", "exp_name": "e"},
            {"type": "cancelled", "source": "s"},
            {"type": "stream_closed", "source": "s"},
            {"type": "workspace_upload_error", "source": "s", "message": "m"},
            {"type": "bohrium_node", "source": "s"},
            {"type": "mcp_server_status", "source": "s", "server_name": "n"},
            {"type": "mcp_connect", "source": "s"},
            {"type": "response_figures", "source": "s", "figures": []},
        ]
        expected_types = [
            AskQuestionEvent,
            AskQuestionReplyEvent,
            AskQuestionTimeoutEvent,
            CompactionEvent,
            ExpRunEvent,
            CancelledEvent,
            StreamClosedEvent,
            WorkspaceUploadErrorEvent,
            BohriumNodeEvent,
            McpServerStatusEvent,
            McpConnectEvent,
            ResponseFiguresEvent,
        ]
        for payload, expected in zip(payloads, expected_types):
            result = _system_event_adapter.validate_python(payload)
            assert isinstance(
                result, expected
            ), f"Expected {expected.__name__}, got {type(result).__name__}"


class TestBusEventUnion:
    def test_validates_all_21_types(self) -> None:
        """BusEvent union can validate all 21 event types."""
        payloads = [
            # 9 AgentEvent types
            {"type": "thought", "source": "a"},
            {"type": "response", "source": "a", "content": "hello"},
            {
                "type": "tool_call",
                "source": "a",
                "call_id": "c",
                "tool_name": "t",
                "arguments": {},
            },
            {
                "type": "tool_result",
                "source": "a",
                "call_id": "c",
                "tool_name": "t",
                "result": "r",
            },
            {"type": "run_result", "source": "a"},
            {"type": "error", "source": "a", "message": "m"},
            {"type": "assistant_state", "source": "a", "state": {}},
            {"type": "skill_hit", "source": "a", "skill_name": "s"},
            {
                "type": "tool_progress",
                "source": "a",
                "call_id": "c",
                "tool_name": "t",
            },
            # 12 SystemEvent types
            {
                "type": "ask_question",
                "source": "s",
                "request_id": "aq_1",
                "questions": [],
            },
            {
                "type": "ask_question_reply",
                "source": "s",
                "request_id": "aq_1",
                "answers": {},
            },
            {
                "type": "ask_question_timeout",
                "source": "s",
                "request_id": "aq_1",
                "questions": [],
            },
            {
                "type": "compaction",
                "source": "context_compactor",
                "compaction_id": "task-1:root:1",
                "status": "running",
                "phase": "runtime",
            },
            {"type": "exp_run", "source": "s", "exp_name": "e"},
            {"type": "cancelled", "source": "s"},
            {"type": "stream_closed", "source": "s"},
            {"type": "workspace_upload_error", "source": "s", "message": "m"},
            {"type": "bohrium_node", "source": "s"},
            {"type": "mcp_server_status", "source": "s", "server_name": "n"},
            {"type": "mcp_connect", "source": "s"},
            {"type": "response_figures", "source": "s", "figures": []},
        ]
        for payload in payloads:
            result = _bus_event_adapter.validate_python(payload)
            assert result.type == payload["type"]

    def test_bus_event_union_accepts_response_event(self) -> None:
        payload = {"type": "response", "source": "agent", "content": "hello"}
        result = _bus_event_adapter.validate_python(payload)
        assert isinstance(result, ResponseEvent)

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            _bus_event_adapter.validate_python({"type": "nonexistent", "source": "x"})

    def test_response_figures_event_round_trips_through_bus_union(self) -> None:
        evt = ResponseFiguresEvent(
            source="System",
            figures=[
                FigureDescriptor(
                    figure_id="band_structure",
                    asset_url="https://oss.example/band.png",
                    caption="Si 的能带图",
                    alt="Si 的能带结构图",
                    importance="primary",
                    placement_hint="sidebar_only",
                    source_tool_call_id="call-band",
                )
            ],
        )

        dumped = evt.model_dump(mode="json")
        restored = TypeAdapter(BusEvent).validate_python(dumped)

        assert isinstance(restored, ResponseFiguresEvent)
        assert restored.figures[0].figure_id == "band_structure"


class TestEventSerializationRoundtrip:
    def test_roundtrip_all_types(self) -> None:
        """For each event type, model_dump -> validate_python returns same class."""
        events = [
            ThoughtEvent(source="a", content="hello"),
            ResponseEvent(source="a", content="hello"),
            ToolCallEvent(source="a", call_id="c", tool_name="t", arguments={}),
            ToolResultEvent(source="a", call_id="c", tool_name="t", result="r"),
            RunResultEvent(source="a"),
            ErrorEvent(source="a", message="m"),
            AssistantStateEvent(source="a", state={"k": "v"}),
            SkillHitEvent(source="a", skill_name="s"),
            ToolProgressEvent(source="a", call_id="c1", tool_name="t1"),
            AskQuestionEvent(source="s", request_id="aq_1", questions=[]),
            AskQuestionReplyEvent(source="s", request_id="aq_1", answers={}),
            AskQuestionTimeoutEvent(source="s", request_id="aq_1", questions=[]),
            CompactionEvent(
                source="context_compactor",
                compaction_id="task-1:root:1",
                status="running",
                phase="runtime",
            ),
            ExpRunEvent(source="s", exp_name="e"),
            CancelledEvent(source="s"),
            StreamClosedEvent(source="s"),
            WorkspaceUploadErrorEvent(source="s", message="m"),
            BohriumNodeEvent(source="s"),
            McpServerStatusEvent(source="s", server_name="n"),
            McpConnectEvent(source="s"),
            ResponseFiguresEvent(source="s", figures=[]),
        ]
        for event in events:
            data = event.model_dump()
            restored = _bus_event_adapter.validate_python(data)
            assert type(restored) is type(
                event
            ), f"Roundtrip failed: {type(event).__name__} -> {type(restored).__name__}"


class TestNoTypeCollision:
    def test_all_21_type_literals_are_unique(self) -> None:
        """All 21 type literals must be globally unique strings."""
        type_values = [
            "thought",
            "response",
            "tool_call",
            "tool_result",
            "run_result",
            "error",
            "assistant_state",
            "skill_hit",
            "tool_progress",
            "ask_question",
            "ask_question_reply",
            "ask_question_timeout",
            "compaction",
            "exp_run",
            "cancelled",
            "stream_closed",
            "workspace_upload_error",
            "bohrium_node",
            "mcp_server_status",
            "mcp_connect",
            "response_figures",
        ]
        assert len(type_values) == 21
        assert len(set(type_values)) == 21


# ── Edge case tests (QUAL-01) ─────────────────────────


_ALL_EVENT_CLASSES = [
    ThoughtEvent,
    ResponseEvent,
    ToolCallEvent,
    ToolResultEvent,
    RunResultEvent,
    ErrorEvent,
    AssistantStateEvent,
    SkillHitEvent,
    ToolProgressEvent,
    AskQuestionEvent,
    AskQuestionReplyEvent,
    AskQuestionTimeoutEvent,
    CompactionEvent,
    ExpRunEvent,
    CancelledEvent,
    StreamClosedEvent,
    WorkspaceUploadErrorEvent,
    BohriumNodeEvent,
    McpServerStatusEvent,
    McpConnectEvent,
    ResponseFiguresEvent,
]


def _make_event_instance(cls):
    """Create a minimal valid instance of each event class."""
    required_extra = {
        ToolCallEvent: {"call_id": "c1", "tool_name": "t1", "arguments": {"k": "v"}},
        ToolResultEvent: {"call_id": "c1", "tool_name": "t1", "result": "ok"},
        ToolProgressEvent: {"call_id": "c1", "tool_name": "t1"},
        ErrorEvent: {"message": "err"},
        AssistantStateEvent: {"state": {"content": "hi"}},
        SkillHitEvent: {"skill_name": "research"},
        AskQuestionEvent: {"request_id": "aq_1", "questions": []},
        AskQuestionReplyEvent: {"request_id": "aq_1", "answers": {}},
        AskQuestionTimeoutEvent: {"request_id": "aq_1", "questions": []},
        CompactionEvent: {
            "compaction_id": "task-1:root:1",
            "status": "running",
            "phase": "runtime",
            "trigger_tokens": 950,
        },
        ExpRunEvent: {"exp_name": "mat_master"},
        WorkspaceUploadErrorEvent: {"message": "upload failed"},
        McpServerStatusEvent: {"server_name": "code-server"},
        ResponseFiguresEvent: {"figures": []},
    }
    kwargs = {"source": "test", **required_extra.get(cls, {})}
    return cls(**kwargs)


class TestBusEventDiscriminatedUnionRoundtrip:
    """QUAL-01: For each event type, create -> dump -> validate -> assert match."""

    def test_bus_event_discriminated_union_roundtrip(self) -> None:
        for cls in _ALL_EVENT_CLASSES:
            event = _make_event_instance(cls)
            data = event.model_dump()
            restored = _bus_event_adapter.validate_python(data)
            assert type(restored) is type(event), (
                f"Roundtrip failed for {cls.__name__}: "
                f"got {type(restored).__name__}"
            )
            # Verify key fields survive roundtrip
            assert restored.type == event.type
            assert restored.source == event.source


class TestThoughtEventStreamStates:
    """QUAL-01: Test stream_state values for ThoughtEvent."""

    def test_thought_event_stream_states(self) -> None:
        for state in ("start", "streaming", "end", None):
            evt = ThoughtEvent(source="agent", stream_state=state)
            assert evt.stream_state == state


class TestEventTimestampAutoPopulated:
    """QUAL-01: Create event without explicit timestamp, assert timestamp is set."""

    def test_event_timestamp_auto_populated(self) -> None:
        for cls in _ALL_EVENT_CLASSES:
            event = _make_event_instance(cls)
            assert isinstance(
                event.timestamp, datetime
            ), f"{cls.__name__}.timestamp not auto-populated"


class TestBusEventInvalidTypeRejected:
    """QUAL-01: Pydantic validation error for unknown type discriminator."""

    def test_bus_event_invalid_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _bus_event_adapter.validate_python(
                {"type": "nonexistent_fake_type", "source": "x"}
            )
