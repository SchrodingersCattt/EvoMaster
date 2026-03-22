"""Integration layer: EventRouter, handlers, and service wrappers.

This package bridges the matmaster kernel/assembly layers with the
existing service layer (agent_run_service, bohrium, workspace upload).
"""

from matmaster.integration.event_router import (
    EventHandler,
    EventRouter,
    PersistenceHandler,
    SSEHandler,
)

__all__ = [
    "EventHandler",
    "EventRouter",
    "PersistenceHandler",
    "SSEHandler",
]
