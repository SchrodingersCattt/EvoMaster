"""Integration layer: RunEventFanout and handler bridges.

This package bridges the matmaster kernel/assembly layers with the
existing service layer (agent_run_service, workspace upload).
"""

from matmaster.integration.fanout import (
    EventHandler,
    RunEventFanout,
)
from matmaster.integration.persistence_handler import PersistenceHandler
from matmaster.integration.sse_handler import SSEHandler
from matmaster.integration.workspace_handler import WorkspaceHandler

__all__ = [
    "EventHandler",
    "PersistenceHandler",
    "RunEventFanout",
    "SSEHandler",
    "WorkspaceHandler",
]
