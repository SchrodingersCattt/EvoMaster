"""Event type hierarchy for the matmaster bus system.

Defines all 18 event types in two categories:
- AgentEvent (8 types): emitted by the kernel during agent execution
- SystemEvent (10 types): emitted by service-layer components

BusEvent = AgentEvent | SystemEvent -- the unified type for MessageBus transport.

All events use Pydantic discriminated union with the ``type`` field (Literal)
as the discriminator, enabling type-safe deserialization from dicts/JSON.
"""

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


class EventBase(BaseModel):
    """Shared fields for all bus events."""

    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    spawn_id: str | None = None


# ── AgentEvent: kernel-layer events ─────────────────────


class ThoughtEvent(EventBase):
    """LLM thought/reasoning event.

    Streaming and non-streaming are unified; use ``stream_state`` to
    distinguish: 'start' | 'streaming' | 'end' | 'complete' | None.
    """

    type: Literal["thought"] = "thought"
    content: str = ""
    stream_state: str | None = None  # 'start' | 'streaming' | 'end' | 'complete' | None
    stream_id: str | None = None
    token_count: int = 0
    context: str | None = None  # e.g. 'step_execution'
    reasoning_content: str | None = None


class ResponseEvent(EventBase):
    """Visible assistant response event."""

    type: Literal["response"] = "response"
    content: str = ""
    stream_state: str | None = None  # 'start' | 'streaming' | 'end' | 'complete' | None
    stream_id: str | None = None


class ToolCallEvent(EventBase):
    """Tool call event -- emitted when the LLM requests a tool invocation."""

    type: Literal["tool_call"] = "tool_call"
    call_id: str
    tool_name: str
    arguments: dict[str, Any]


class ToolResultEvent(EventBase):
    """Tool execution result event."""

    type: Literal["tool_result"] = "tool_result"
    call_id: str
    tool_name: str
    result: Any  # str | dict
    status: str = "success"
    info: dict[str, Any] = Field(default_factory=dict)


class RunResultEvent(EventBase):
    """Business terminal event for a run outcome.

    Canonical type is ``run_result``. Legacy ``finish`` payloads are still
    accepted during migration so persisted history can be replayed.
    """

    type: Literal["run_result", "finish"] = "run_result"
    status: str = "completed"  # 'completed' | 'failed' | 'cancelled'
    reason: str = ""
    final_content: str | None = None


class ErrorEvent(EventBase):
    """Agent execution error event."""

    type: Literal["error"] = "error"
    message: str
    traceback: str | None = None


class AssistantStateEvent(EventBase):
    """Full assistant message state (including tool_calls list) for persistence."""

    type: Literal["assistant_state"] = "assistant_state"
    state: dict[str, Any]  # AssistantMessage.model_dump() content


class SkillHitEvent(EventBase):
    """Skill hit tracking event."""

    type: Literal["skill_hit"] = "skill_hit"
    skill_name: str


# ── SystemEvent: service-layer events ───────────────────


class ConfirmationRequestEvent(EventBase):
    """User confirmation request event."""

    type: Literal["confirmation_request"] = "confirmation_request"
    question: str
    mode: str  # 'timeout' | 'block'
    timeout_seconds: int | None = None
    context: str | None = None
    actions: list[str] = Field(default_factory=list)
    origin: str | None = None


class ConfirmationTimeoutEvent(EventBase):
    """Confirmation timeout event."""

    type: Literal["confirmation_timeout"] = "confirmation_timeout"
    question: str
    default_reply: str | None = None


class ContextCompactionEvent(EventBase):
    """Context compaction event."""

    type: Literal["context_compaction"] = "context_compaction"
    payload: dict[str, Any]


class ExpRunEvent(EventBase):
    """Experiment run event."""

    type: Literal["exp_run"] = "exp_run"
    exp_name: str


class CancelledEvent(EventBase):
    """Agent execution cancelled event."""

    type: Literal["cancelled"] = "cancelled"
    reason: str = ""


class StreamClosedEvent(EventBase):
    """Transport-level marker indicating the live SSE stream can close.

    Canonical type is ``stream_closed``. Legacy ``end`` payloads are still
    accepted during migration so old live/history payloads remain readable.
    """

    type: Literal["stream_closed", "end"] = "stream_closed"
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
        SkillHitEvent,
    ],
    Field(discriminator="type"),
]

SystemEvent = Annotated[
    Union[
        ConfirmationRequestEvent,
        ConfirmationTimeoutEvent,
        ContextCompactionEvent,
        ExpRunEvent,
        CancelledEvent,
        StreamClosedEvent,
        WorkspaceUploadErrorEvent,
        BohriumNodeEvent,
        McpServerStatusEvent,
        McpConnectEvent,
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
        SkillHitEvent,
        # SystemEvent types
        ConfirmationRequestEvent,
        ConfirmationTimeoutEvent,
        ContextCompactionEvent,
        ExpRunEvent,
        CancelledEvent,
        StreamClosedEvent,
        WorkspaceUploadErrorEvent,
        BohriumNodeEvent,
        McpServerStatusEvent,
        McpConnectEvent,
    ],
    Field(discriminator="type"),
]


# Legacy aliases kept during protocol migration. New code should use the
# clearer RunResultEvent / StreamClosedEvent names.
FinishEvent = RunResultEvent
EndEvent = StreamClosedEvent
