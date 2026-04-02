"""Integration layer: EventRouter and handler bridges.

This package bridges the matmaster kernel/assembly layers with the
existing service layer (agent_run_service, workspace upload).
"""

from matmaster.integration.event_router import (
    EventHandler,
    EventRouter,
)
from matmaster.integration.persistence_handler import PersistenceHandler
from matmaster.integration.sse_handler import SSEHandler
from matmaster.integration.workspace_handler import WorkspaceHandler

__all__ = [
    "EventHandler",
    "EventRouter",
    "PersistenceHandler",
    "SSEHandler",
    "WorkspaceHandler",
]
