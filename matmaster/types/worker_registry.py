"""WorkerRegistry Protocol -- session_run_owner management interface.

Phase 3 defines the Protocol only. Phase 5 provides the Redis-backed
implementation. Service layer injects the implementation into Exp via
constructor or PlaygroundContext.run_meta.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class WorkerRegistry(Protocol):
    """Session run ownership management.

    Tracks which worker (pod) owns a session's current run.
    Used for:
    - Preventing duplicate runs on the same session
    - Detecting run_interrupted (different worker takes over)
    - Coordinating cross-pod session handoff

    Methods mirror the existing worker_registry_service.py interface
    (src/services/worker_registry_service.py).
    """

    def set_session_run_owner(self, session_id: str, worker_id: str) -> bool:
        """Claim ownership of a session run. Returns True if successful."""
        ...

    def refresh_session_run_owner(self, session_id: str, worker_id: str) -> bool:
        """Refresh ownership TTL. Returns True if still owner."""
        ...

    def delete_session_run_owner(self, session_id: str) -> bool:
        """Release ownership. Returns True if deleted."""
        ...

    def get_session_run_owner(self, session_id: str) -> str | None:
        """Get current owner worker_id, or None if no owner."""
        ...
