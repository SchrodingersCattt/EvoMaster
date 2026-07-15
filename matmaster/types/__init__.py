"""matmaster.types -- Type contracts for the three-layer architecture."""

from typing import Any

from .cancellation import CancellationController, CancellationToken, CancelledError
from .errors import LLMError
from .events import (
    AgentEvent,
    AssistantStateEvent,
    BohriumNodeEvent,
    BusEvent,
    CancelledEvent,
    CompactionEvent,
    ErrorEvent,
    ExpRunEvent,
    FinishDetail,
    InteractionReplyEvent,
    InteractionRequestEvent,
    InteractionTimeoutEvent,
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
from .figures import FigureDescriptor, FigureUploadConfig
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
    InterruptChecker,
    KernelRuntimePorts,
    PlaygroundCompactionPort,
    PreCompactionBarrier,
    SessionEventHistoryPort,
    ToolTimeoutNotice,
    ToolTimeoutObserver,
)
from .session import LocalSessionConfig, Session, SessionConfig, SSHSessionConfig
from .tool_decision import ToolDecision
from .tool_spec import ResourceClaim, ToolBinding, ToolInstance, ToolSpec
from .topology import RuntimeTopology, SessionCapabilities, ToolPlane
from .worker_registry import WorkerRegistry

_RUNTIME_EXPORTS = frozenset(
    {
        "AgentKernelSpec",
        "AgentKernelResources",
        "AgentKernelRuntime",
        "CompactionConfig",
    }
)


def __getattr__(name: str) -> Any:
    if name in _RUNTIME_EXPORTS:
        from .runtime import (
            AgentKernelResources,
            AgentKernelRuntime,
            AgentKernelSpec,
            CompactionConfig,
        )

        exports = {
            "AgentKernelSpec": AgentKernelSpec,
            "AgentKernelResources": AgentKernelResources,
            "AgentKernelRuntime": AgentKernelRuntime,
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
    "AssistantStateEvent",
    "BohriumNodeEvent",
    "BusEvent",
    "CancelledEvent",
    "CompactionEvent",
    "ErrorEvent",
    "ExpRunEvent",
    "FinishDetail",
    "FigureDescriptor",
    "FigureUploadConfig",
    "InteractionReplyEvent",
    "InteractionRequestEvent",
    "InteractionTimeoutEvent",
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
    "AgentKernelSpec",
    "AgentKernelResources",
    "AgentKernelRuntime",
    "BusEventSink",
    "CheckpointSink",
    "CheckpointSinkFactory",
    "CompactionConfig",
    "EmptySessionEventHistory",
    "InterruptChecker",
    "KernelRuntimePorts",
    "PlaygroundCompactionPort",
    "PreCompactionBarrier",
    "SessionEventHistoryPort",
    "ToolTimeoutNotice",
    "ToolTimeoutObserver",
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
