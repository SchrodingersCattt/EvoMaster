"""Event bus package -- synchronous message bus for agent event delivery.

Provides:
- MessageBus: thread-safe synchronous event queue (queue.Queue wrapper)

Note: QueueBridge (SSE payload conversion) was removed in Phase 7.
SSE delivery is handled by SSEHandler in matmaster/integration/event_router.py.
"""

from .queue import MessageBus

__all__ = ["MessageBus"]
