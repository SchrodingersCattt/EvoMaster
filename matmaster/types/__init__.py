"""matmaster.types -- Type contracts for the three-layer architecture."""

from .cancellation import CancellationController, CancellationToken, CancelledError
from .context import PlaygroundContext
from .errors import LLMError
from .events import (
    AgentEvent,
    AskQuestionEvent,
    AskQuestionReplyEvent,
    AskQuestionTimeoutEvent,
    AssistantStateEvent,
    BohriumNodeEvent,
    BusEvent,
    CancelledEvent,
    CompactionEvent,
    ConfirmationRequestEvent,
    ConfirmationTimeoutEvent,
    ErrorEvent,
    ExpRunEvent,
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
from .figures import FigureDescriptor, FigureManifestEntry, FigureUploadConfig
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
    # cancellation
    "CancellationController",
    "CancellationToken",
    "CancelledError",
    # context
    "PlaygroundContext",
    # errors
    "LLMError",
    # events
    "AgentEvent",
    "AskQuestionEvent",
    "AskQuestionReplyEvent",
    "AskQuestionTimeoutEvent",
    "AssistantStateEvent",
    "BohriumNodeEvent",
    "BusEvent",
    "CancelledEvent",
    "CompactionEvent",
    "ConfirmationRequestEvent",
    "ConfirmationTimeoutEvent",
    "ErrorEvent",
    "ExpRunEvent",
    "FigureDescriptor",
    "FigureManifestEntry",
    "FigureUploadConfig",
    "McpConnectEvent",
    "McpServerStatusEvent",
    "ResponseEvent",
    "ResponseFiguresEvent",
    "RunResultEvent",
    "SkillHitEvent",
    "StreamClosedEvent",
    "SystemEvent",
    "ThoughtEvent",
    "ToolCallEvent",
    "ToolProgressEvent",
    "ToolResultEvent",
    "WorkspaceUploadErrorEvent",
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
