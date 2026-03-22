"""Tests for WorkerRegistry Protocol -- session_run_owner management interface.

Covers: @runtime_checkable isinstance checks (conforming/non-conforming),
mock implementation correctness (set/refresh/delete/get).
"""

from __future__ import annotations

from matmaster.assembly.worker_registry import WorkerRegistry


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class MockWorkerRegistry:
    """Dict-backed WorkerRegistry implementation for testing.

    Mirrors the interface of src/services/worker_registry_service.py.
    """

    def __init__(self) -> None:
        self._owners: dict[str, str] = {}

    def set_session_run_owner(self, session_id: str, worker_id: str) -> bool:
        """Claim ownership. Always succeeds in mock."""
        self._owners[session_id] = worker_id
        return True

    def refresh_session_run_owner(self, session_id: str, worker_id: str) -> bool:
        """Refresh ownership. Returns True only if caller is current owner."""
        if self._owners.get(session_id) == worker_id:
            return True
        return False

    def delete_session_run_owner(self, session_id: str) -> bool:
        """Release ownership. Returns True if session was owned."""
        if session_id in self._owners:
            del self._owners[session_id]
            return True
        return False

    def get_session_run_owner(self, session_id: str) -> str | None:
        """Get current owner, or None."""
        return self._owners.get(session_id)


class IncompleteRegistry:
    """Missing delete_session_run_owner -- should fail isinstance check."""

    def set_session_run_owner(self, session_id: str, worker_id: str) -> bool:
        return True

    def refresh_session_run_owner(self, session_id: str, worker_id: str) -> bool:
        return True

    def get_session_run_owner(self, session_id: str) -> str | None:
        return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_protocol_isinstance() -> None:
    """A class implementing all 4 methods passes isinstance check."""
    registry = MockWorkerRegistry()
    assert isinstance(registry, WorkerRegistry)


def test_protocol_missing_method() -> None:
    """A class missing one method fails isinstance check."""
    incomplete = IncompleteRegistry()
    assert not isinstance(incomplete, WorkerRegistry)


def test_mock_implementation() -> None:
    """MockWorkerRegistry set/refresh/delete/get round-trip works."""
    reg = MockWorkerRegistry()

    # Set owner
    assert reg.set_session_run_owner("session-1", "worker-A") is True
    assert reg.get_session_run_owner("session-1") == "worker-A"

    # Refresh by current owner succeeds
    assert reg.refresh_session_run_owner("session-1", "worker-A") is True

    # Refresh by different worker fails
    assert reg.refresh_session_run_owner("session-1", "worker-B") is False

    # Delete
    assert reg.delete_session_run_owner("session-1") is True
    assert reg.get_session_run_owner("session-1") is None


def test_get_nonexistent() -> None:
    """get_session_run_owner for unknown session_id returns None."""
    reg = MockWorkerRegistry()
    assert reg.get_session_run_owner("nonexistent") is None
