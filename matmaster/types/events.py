"""Event type hierarchy for the matmaster event system.

Defines all 23 event types in two categories:
- AgentEvent (10 types): emitted by the kernel during agent execution
- SystemEvent (13 types): emitted by service-layer components

BusEvent = AgentEvent | SystemEvent -- the unified event union type.

All events use Pydantic discriminated union with the ``type`` field (Literal)
as the discriminator, enabling type-safe deserialization from dicts/JSON.
"""

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from .figures import FigureDescriptor
from .messages import ImageContentPart


class EventBase(BaseModel):
    """Shared fields for all bus events."""

    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    spawn_id: str | None = None


# ── AgentEvent: kernel-layer events ─────────────────────


class TurnUsageCarrierEvent(EventBase):
    """模型输出侧事件共享的 accepted-turn usage 载体字段。

    同一 accepted turn 的多个事件携带相同 turn_index 与相同 usage 快照；
    消费方须按 turn_index 去重。
    """

    turn_index: int | None = None
    turn_usage: dict[str, int] = Field(default_factory=dict)
    total_usage: dict[str, int] = Field(default_factory=dict)
    usage_vendor: dict[str, Any] | None = None


class ThoughtEvent(TurnUsageCarrierEvent):
    """LLM thought/reasoning event.

    Streaming and non-streaming are unified; use ``stream_state`` to
    distinguish: 'start' | 'streaming' | 'segment_end' | 'end' | 'complete' | None.
    'complete' is the accepted-turn reasoning audit event and may carry usage;
    all other states are ephemeral streaming/segment markers without usage.
    """

    type: Literal["thought"] = "thought"
    content: str = ""
    stream_state: str | None = (
        None  # 'start' | 'streaming' | 'segment_end' | 'end' | 'complete' | None
    )
    stream_id: str | None = None
    token_count: int = 0
    context: str | None = None  # e.g. 'step_execution'
    reasoning_content: str | None = None


class ResponseEvent(TurnUsageCarrierEvent):
    """Visible assistant response event."""

    type: Literal["response"] = "response"
    content: str = ""
    stream_state: str | None = None  # 'start' | 'streaming' | 'end' | 'complete' | None
    stream_id: str | None = None
    model: str | None = None
    model_profile: str | None = None
    model_route: str | None = None


class ToolCallEvent(TurnUsageCarrierEvent):
    """Tool call event -- emitted when the LLM requests a tool invocation.

    Usage fields describe the accepted LLM turn that requested the calls,
    not the tool execution itself.
    """

    type: Literal["tool_call"] = "tool_call"
    call_id: str
    tool_name: str
    arguments: dict[str, Any]


class ToolResultEvent(EventBase):
    """Tool execution result event.

    Carries only the tool execution outcome. LLM token usage lives on the
    model-output-side events (thought.complete / response.complete /
    tool_call); tool payloads may still embed tool-specific evidence such
    as ``payload["subagent_usage"]``.
    """

    type: Literal["tool_result"] = "tool_result"
    call_id: str
    tool_name: str
    result: Any  # str | dict
    status: str = "success"
    payload: dict[str, Any] = Field(default_factory=dict)
    images: list[ImageContentPart] = Field(default_factory=list)


class FinishDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "output_length_exceeded",
        "content_filtered",
        "empty_response",
        "reasoning_only",
        "missing_llm_response",
        "missing_tool_call_payload",
        "non_stop_finish",
        "unknown",
    ]
    provider_finish_reason: str | None = None
    message: str
    content_chars: int = 0
    reasoning_chars: int = 0
    has_visible_content: bool = False
    has_reasoning: bool = False
    has_tool_calls: bool = False
    tool_call_count: int = 0
    last_turn_usage: dict[str, int] = Field(default_factory=dict)
    last_turn_usage_vendor: dict[str, Any] = Field(default_factory=dict)
    attempts: int | None = None
    last_error_kind: str | None = None
    truncation_risk: bool = False


class RunResultEvent(EventBase):
    """Business terminal event for a run outcome."""

    type: Literal["run_result"] = "run_result"
    status: str = "completed"  # 'completed' | 'failed' | 'cancelled'
    reason: str = ""
    final_content: str | None = None
    num_turns: int = 0
    usage: dict[str, int] = Field(default_factory=dict)
    usage_vendor_by_turn: list[dict[str, Any]] = Field(default_factory=list)
    finish_detail: FinishDetail | None = None
    model: str | None = None
    model_profile: str | None = None
    model_route: str | None = None
    # exclude=True: messages carries the full conversation transcript
    # (including system prompt) for internal drain consumers only.
    # model_dump() excludes it, so SSE/frontend never sees it.
    messages: list[Any] = Field(default_factory=list, exclude=True)


class ErrorEvent(EventBase):
    """Agent execution error event."""

    type: Literal["error"] = "error"
    message: str
    traceback: str | None = None


class AssistantStateEvent(EventBase):
    """Full assistant message state (including tool_calls list) for persistence."""

    type: Literal["assistant_state"] = "assistant_state"
    state: dict[str, Any]  # AssistantMessage.model_dump() content
    turn_index: int | None = None
    turn_usage: dict[str, int] = Field(default_factory=dict)
    total_usage: dict[str, int] = Field(default_factory=dict)
    finish_detail: FinishDetail | None = None
    model: str | None = None
    model_profile: str | None = None
    model_route: str | None = None


class CheckpointEvent(EventBase):
    """Emitted between LLM response and tool dispatch to allow user interrupt."""

    type: Literal["checkpoint"] = "checkpoint"
    turn_index: int | None = None


class SkillHitEvent(EventBase):
    """Skill hit tracking event."""

    type: Literal["skill_hit"] = "skill_hit"
    skill_name: str


class ToolProgressEvent(EventBase):
    """Streamed progress from a running tool (e.g., bash stdout lines)."""

    type: Literal["tool_progress"] = "tool_progress"
    call_id: str
    tool_name: str
    content: str = ""


# ── SystemEvent: service-layer events ───────────────────


class AskQuestionEvent(EventBase):
    """Structured multi-choice question event sent to user."""

    type: Literal["ask_question"] = "ask_question"
    request_id: str
    questions: list[dict[str, Any]]
    metadata: dict[str, Any] = Field(default_factory=dict)
    origin: str | None = None
    preview_format: str = "markdown"


class AskQuestionReplyEvent(EventBase):
    """User reply to a structured question."""

    type: Literal["ask_question_reply"] = "ask_question_reply"
    request_id: str
    answers: dict[str, str]
    annotations: dict[str, dict[str, str]] = Field(default_factory=dict)


class AskQuestionTimeoutEvent(EventBase):
    """Structured question timeout event."""

    type: Literal["ask_question_timeout"] = "ask_question_timeout"
    request_id: str
    questions: list[dict[str, Any]]
    reason: str = "timeout"


class CompactionEvent(EventBase):
    """Public compaction lifecycle event."""

    type: Literal["compaction"] = "compaction"
    compaction_id: str
    status: Literal["running", "complete", "interrupted"]
    phase: Literal["preflight", "runtime"]
    strategy: Literal["summary", "sliding_window", "tool_truncation"] | None = None
    durability: Literal["durable", "ephemeral"] | None = None
    trigger_tokens: int | None = None
    retained_turns: int | None = None
    checkpoint_written: bool | None = None
    failure_reason: str | None = None
    covered_until_event_id: int | None = None
    turn_usage: dict[str, int] | None = None
    total_usage: dict[str, int] | None = None


class ExpRunEvent(EventBase):
    """Experiment run event."""

    type: Literal["exp_run"] = "exp_run"
    exp_name: str


class CancelledEvent(EventBase):
    """Agent execution cancelled event."""

    type: Literal["cancelled"] = "cancelled"
    reason: str = ""


class StreamClosedEvent(EventBase):
    """Transport-level marker indicating the live SSE stream can close."""

    type: Literal["stream_closed"] = "stream_closed"
    content: str = ""
    task_completed: bool = False
    end_reason: str | None = None
    treat_as_failure: bool | None = None


class WorkspaceUploadErrorEvent(EventBase):
    """Workspace upload error event."""

    type: Literal["workspace_upload_error"] = "workspace_upload_error"
    message: str


class BohriumNodeEvent(EventBase):
    """Bohrium node status event."""

    type: Literal["bohrium_node"] = "bohrium_node"
    payload: dict[str, Any] = Field(default_factory=dict)


class McpServerStatusEvent(EventBase):
    """MCP server status event."""

    type: Literal["mcp_server_status"] = "mcp_server_status"
    server_name: str
    transport: str | None = None
    phase: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class McpConnectEvent(EventBase):
    """MCP connection lifecycle event."""

    type: Literal["mcp_connect"] = "mcp_connect"
    phase: str = ""  # 'start' | 'ready' | 'failed'
    message: str = ""
    elapsed_ms: int | None = None
    error: str | None = None


class ResponseFiguresEvent(EventBase):
    """Image metadata emitted alongside a chat response."""

    type: Literal["response_figures"] = "response_figures"
    figures: list[FigureDescriptor] = Field(default_factory=list)


class SubagentSpawnEvent(EventBase):
    """Spawn 绑定事件:宣告 spawn_id 与父 Agent 工具调用的对应关系。"""

    type: Literal["subagent_spawn"] = "subagent_spawn"
    parent_call_id: str | None = None
    exp_name: str
    task_summary: str = ""


# ── Union definitions ───────────────────────────────────

AgentEvent = Annotated[
    Union[
        ThoughtEvent,
        ResponseEvent,
        ToolCallEvent,
        ToolResultEvent,
        RunResultEvent,
        ErrorEvent,
        AssistantStateEvent,
        CheckpointEvent,
        SkillHitEvent,
        ToolProgressEvent,
    ],
    Field(discriminator="type"),
]

SystemEvent = Annotated[
    Union[
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
        SubagentSpawnEvent,
    ],
    Field(discriminator="type"),
]

BusEvent = Annotated[
    Union[
        # AgentEvent types
        ThoughtEvent,
        ResponseEvent,
        ToolCallEvent,
        ToolResultEvent,
        RunResultEvent,
        ErrorEvent,
        AssistantStateEvent,
        CheckpointEvent,
        SkillHitEvent,
        ToolProgressEvent,
        # SystemEvent types
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
        SubagentSpawnEvent,
    ],
    Field(discriminator="type"),
]
