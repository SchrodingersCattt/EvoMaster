"""Integration layer: EventRouter, handlers, and service wrappers.

This package bridges the matmaster kernel/assembly layers with the
existing service layer (agent_run_service, bohrium, workspace upload).
"""

from matmaster.integration.bohrium_setup import BohriumSetupService
from matmaster.integration.event_router import (
    EventHandler,
    EventRouter,
    PersistenceHandler,
    SSEHandler,
)
from matmaster.integration.workspace_handler import WorkspaceHandler

__all__ = [
    "BohriumSetupService",
    "EventHandler",
    "EventRouter",
    "PersistenceHandler",
    "SSEHandler",
    "WorkspaceHandler",
]
