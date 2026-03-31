"""Tests for WorkerRegistry Protocol -- session_run_owner management interface.

Covers: @runtime_checkable isinstance checks (conforming/non-conforming),
mock implementation correctness (set/refresh/delete/get).
"""

from __future__ import annotations

from matmaster.types.worker_registry import WorkerRegistry

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


# ---------------------------------------------------------------------------
# WorkerRegistryServiceAdapter tests
# ---------------------------------------------------------------------------


class _StubService:
    """Minimal stub mimicking WorkerRegistryService interface."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def set_session_run_owner(self, session_id: str, worker_id: str) -> bool:
        self.calls.append(("set", session_id, worker_id))
        return True

    def refresh_session_run_owner(self, session_id: str, worker_id: str) -> bool:
        self.calls.append(("refresh", session_id, worker_id))
        return True

    def delete_session_run_owner(self, session_id: str) -> None:
        self.calls.append(("delete", session_id))
        return None  # Service returns None, not bool

    def get_session_run_owner(self, session_id: str) -> str | None:
        self.calls.append(("get", session_id))
        return "worker-X"


class TestWorkerRegistryServiceAdapter:
    """WorkerRegistryServiceAdapter bridges Service -> Protocol."""

    def test_adapter_isinstance_check(self) -> None:
        """Adapter passes WorkerRegistry isinstance check."""
        from src.services.worker_registry_adapter import WorkerRegistryServiceAdapter

        adapter = WorkerRegistryServiceAdapter(_StubService())
        assert isinstance(adapter, WorkerRegistry)

    def test_adapter_delete_returns_bool(self) -> None:
        """Adapter bridges None -> True for delete."""
        from src.services.worker_registry_adapter import WorkerRegistryServiceAdapter

        adapter = WorkerRegistryServiceAdapter(_StubService())
        result = adapter.delete_session_run_owner("s1")
        assert result is True

    def test_adapter_delegates_set(self) -> None:
        """Adapter delegates set_session_run_owner to service."""
        from src.services.worker_registry_adapter import WorkerRegistryServiceAdapter

        svc = _StubService()
        adapter = WorkerRegistryServiceAdapter(svc)
        result = adapter.set_session_run_owner("s1", "w1")
        assert result is True
        assert ("set", "s1", "w1") in svc.calls

    def test_adapter_delegates_refresh(self) -> None:
        """Adapter delegates refresh_session_run_owner to service."""
        from src.services.worker_registry_adapter import WorkerRegistryServiceAdapter

        svc = _StubService()
        adapter = WorkerRegistryServiceAdapter(svc)
        result = adapter.refresh_session_run_owner("s1", "w1")
        assert result is True
        assert ("refresh", "s1", "w1") in svc.calls

    def test_adapter_delegates_get(self) -> None:
        """Adapter delegates get_session_run_owner to service."""
        from src.services.worker_registry_adapter import WorkerRegistryServiceAdapter

        svc = _StubService()
        adapter = WorkerRegistryServiceAdapter(svc)
        result = adapter.get_session_run_owner("s1")
        assert result == "worker-X"
        assert ("get", "s1") in svc.calls
