"""matmaster.contracts -- Type contracts for the three-layer architecture."""

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
from .guards import Guard, GuardContext, GuardResult, RecentCall
from .runtime import AgentRuntimeSpec, CompactionConfig

__all__ = [
    # events
    "AgentEvent",
    "AssistantStateEvent",
    "BohriumNodeEvent",
    "BusEvent",
    "CancelledEvent",
    "ConfirmationRequestEvent",
    "ConfirmationTimeoutEvent",
    "ContextCompactionEvent",
    "ErrorEvent",
    "ExpRunEvent",
    "FinishEvent",
    "McpConnectEvent",
    "McpServerStatusEvent",
    "SkillHitEvent",
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
    # context
    "PlaygroundContext",
    # runtime
    "AgentRuntimeSpec",
    "CompactionConfig",
]
