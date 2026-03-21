"""Event type hierarchy for the matmaster bus system.

Defines all 16 event types in two categories:
- AgentEvent (7 types): emitted by the kernel during agent execution
- SystemEvent (9 types): emitted by service-layer components

BusEvent = AgentEvent | SystemEvent -- the unified type for MessageBus transport.

All events use Pydantic discriminated union with the ``type`` field (Literal)
as the discriminator, enabling type-safe deserialization from dicts/JSON.
"""

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


# ── AgentEvent: kernel-layer events ─────────────────────


class ThoughtEvent(BaseModel):
    """LLM thought/reasoning event.

    Streaming and non-streaming are unified; use ``stream_state`` to
    distinguish: 'start' | 'streaming' | 'end' | None (non-streaming).
    """

    type: Literal["thought"] = "thought"
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    content: str = ""
    stream_state: str | None = None  # 'start' | 'streaming' | 'end' | None
    stream_id: str | None = None
    token_count: int = 0
    context: str | None = None  # e.g. 'step_execution'
    reasoning_content: str | None = None


class ToolCallEvent(BaseModel):
    """Tool call event -- emitted when the LLM requests a tool invocation."""

    type: Literal["tool_call"] = "tool_call"
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    call_id: str
    tool_name: str
    arguments: dict[str, Any]


class ToolResultEvent(BaseModel):
    """Tool execution result event."""

    type: Literal["tool_result"] = "tool_result"
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    call_id: str
    tool_name: str
    result: Any  # str | dict
    info: dict[str, Any] = Field(default_factory=dict)


class FinishEvent(BaseModel):
    """Agent execution completion event."""

    type: Literal["finish"] = "finish"
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    status: str = "completed"  # 'completed' | 'failed' | 'cancelled'
    reason: str = ""
    final_content: str | None = None


class ErrorEvent(BaseModel):
    """Agent execution error event."""

    type: Literal["error"] = "error"
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    message: str
    traceback: str | None = None


class AssistantStateEvent(BaseModel):
    """Full assistant message state (including tool_calls list) for persistence."""

    type: Literal["assistant_state"] = "assistant_state"
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    state: dict[str, Any]  # AssistantMessage.model_dump() content


class SkillHitEvent(BaseModel):
    """Skill hit tracking event."""

    type: Literal["skill_hit"] = "skill_hit"
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    skill_name: str


# ── SystemEvent: service-layer events ───────────────────


class ConfirmationRequestEvent(BaseModel):
    """User confirmation request event."""

    type: Literal["confirmation_request"] = "confirmation_request"
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    question: str
    mode: str  # 'timeout' | 'block'
    timeout_seconds: int | None = None
    context: str | None = None
    actions: list[str] = Field(default_factory=list)
    origin: str | None = None


class ConfirmationTimeoutEvent(BaseModel):
    """Confirmation timeout event."""

    type: Literal["confirmation_timeout"] = "confirmation_timeout"
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    question: str
    default_reply: str | None = None


class ContextCompactionEvent(BaseModel):
    """Context compaction event."""

    type: Literal["context_compaction"] = "context_compaction"
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    payload: dict[str, Any]


class ExpRunEvent(BaseModel):
    """Experiment run event."""

    type: Literal["exp_run"] = "exp_run"
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    exp_name: str


class CancelledEvent(BaseModel):
    """Agent execution cancelled event."""

    type: Literal["cancelled"] = "cancelled"
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    reason: str = ""


class WorkspaceUploadErrorEvent(BaseModel):
    """Workspace upload error event."""

    type: Literal["workspace_upload_error"] = "workspace_upload_error"
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    message: str


class BohriumNodeEvent(BaseModel):
    """Bohrium node status event."""

    type: Literal["bohrium_node"] = "bohrium_node"
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    payload: dict[str, Any] = Field(default_factory=dict)


class McpServerStatusEvent(BaseModel):
    """MCP server status event."""

    type: Literal["mcp_server_status"] = "mcp_server_status"
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    server_name: str
    transport: str | None = None
    phase: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class McpConnectEvent(BaseModel):
    """MCP connection lifecycle event."""

    type: Literal["mcp_connect"] = "mcp_connect"
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    phase: str = ""  # 'start' | 'ready' | 'failed'
    message: str = ""
    elapsed_ms: int | None = None
    error: str | None = None


# ── Union definitions ───────────────────────────────────

AgentEvent = Annotated[
    Union[
        ThoughtEvent,
        ToolCallEvent,
        ToolResultEvent,
        FinishEvent,
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
        ToolCallEvent,
        ToolResultEvent,
        FinishEvent,
        ErrorEvent,
        AssistantStateEvent,
        SkillHitEvent,
        # SystemEvent types
        ConfirmationRequestEvent,
        ConfirmationTimeoutEvent,
        ContextCompactionEvent,
        ExpRunEvent,
        CancelledEvent,
        WorkspaceUploadErrorEvent,
        BohriumNodeEvent,
        McpServerStatusEvent,
        McpConnectEvent,
    ],
    Field(discriminator="type"),
]
