"""Event bus package -- synchronous message bus and SSE bridge.

Provides:
- MessageBus: thread-safe synchronous event queue (queue.Queue wrapper)
- QueueBridge: converts BusEvent to SSE payload dict format
"""

from .queue import MessageBus

__all__ = ["MessageBus"]
