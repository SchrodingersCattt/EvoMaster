"""matmaster.types -- Type contracts for the three-layer architecture."""

from .context import PlaygroundContext
from .events import (
    AgentEvent,
    AssistantStateEvent,
    BohriumNodeEvent,
    BusEvent,
    CancelledEvent,
    ConfirmationRequestEvent,
    ConfirmationTimeoutEvent,
    ContextCompactionEvent,
    EndEvent,
    ErrorEvent,
    ExpRunEvent,
    FinishEvent,
    McpConnectEvent,
    McpServerStatusEvent,
    ResponseEvent,
    RunResultEvent,
    SkillHitEvent,
    StreamClosedEvent,
    SystemEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
    WorkspaceUploadErrorEvent,
)
from .guards import Guard, GuardContext, GuardResult, RecentCall
from .llm_provider import LLMProvider
from .messages import (
    AssistantMessage,
    LLMResponse,
    Message,
    Role,
    StreamChunk,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)
from .runtime import AgentRuntimeSpec, CompactionConfig
from .session import LocalSessionConfig, Session, SessionConfig, SSHSessionConfig
from .tool_decision import ToolDecision
from .tool_spec import ResourceClaim, ToolBinding, ToolInstance, ToolSpec
from .topology import RuntimeTopology, SessionCapabilities, ToolPlane
from .worker_registry import WorkerRegistry

__all__ = [
    # context
    "PlaygroundContext",
    # events
    "AgentEvent",
    "AssistantStateEvent",
    "BohriumNodeEvent",
    "BusEvent",
    "CancelledEvent",
    "ConfirmationRequestEvent",
    "ConfirmationTimeoutEvent",
    "ContextCompactionEvent",
    "EndEvent",
    "ErrorEvent",
    "ExpRunEvent",
    "FinishEvent",
    "McpConnectEvent",
    "McpServerStatusEvent",
    "ResponseEvent",
    "RunResultEvent",
    "SkillHitEvent",
    "StreamClosedEvent",
    "SystemEvent",
    "ThoughtEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "WorkspaceUploadErrorEvent",
    # guards
    "Guard",
    "GuardContext",
    "GuardResult",
    "RecentCall",
    # llm
    "LLMProvider",
    # messages
    "AssistantMessage",
    "LLMResponse",
    "Message",
    "Role",
    "StreamChunk",
    "SystemMessage",
    "ToolCallData",
    "ToolMessage",
    "UserMessage",
    # runtime
    "AgentRuntimeSpec",
    "CompactionConfig",
    # tool runtime v2
    "ToolPlane",
    "SessionCapabilities",
    "RuntimeTopology",
    "ToolSpec",
    "ResourceClaim",
    "ToolBinding",
    "ToolInstance",
    "ToolDecision",
    # session
    "Session",
    "SessionConfig",
    "LocalSessionConfig",
    "SSHSessionConfig",
    # worker registry
    "WorkerRegistry",
]
