"""matmaster.types -- Type contracts for the three-layer architecture."""

from typing import Any

from .cancellation import CancellationController, CancellationToken, CancelledError
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
from .runtime_ports import (
    AgentRunPorts,
    BusEventSink,
    CheckpointSink,
    CheckpointSinkFactory,
    EmptySessionEventHistory,
    KernelRuntimePorts,
    PlaygroundCompactionPort,
    PreCompactionBarrier,
    SessionEventHistoryPort,
)
from .session import LocalSessionConfig, Session, SessionConfig, SSHSessionConfig
from .tool_decision import ToolDecision
from .tool_spec import ResourceClaim, ToolBinding, ToolInstance, ToolSpec
from .topology import RuntimeTopology, SessionCapabilities, ToolPlane
from .worker_registry import WorkerRegistry

_RUNTIME_EXPORTS = frozenset({"AgentRuntimeSpec", "CompactionConfig"})


def __getattr__(name: str) -> Any:
    if name in _RUNTIME_EXPORTS:
        from .runtime import AgentRuntimeSpec, CompactionConfig

        exports = {
            "AgentRuntimeSpec": AgentRuntimeSpec,
            "CompactionConfig": CompactionConfig,
        }
        globals().update(exports)
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # cancellation
    "CancellationController",
    "CancellationToken",
    "CancelledError",
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
    "ErrorEvent",
    "ExpRunEvent",
    "FinishDetail",
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
    "AgentRunPorts",
    "AgentRuntimeSpec",
    "BusEventSink",
    "CheckpointSink",
    "CheckpointSinkFactory",
    "CompactionConfig",
    "EmptySessionEventHistory",
    "KernelRuntimePorts",
    "PlaygroundCompactionPort",
    "PreCompactionBarrier",
    "SessionEventHistoryPort",
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
