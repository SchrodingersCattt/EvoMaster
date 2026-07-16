from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from matmaster.bohrium.types import BohriumRuntimeSnapshot
from matmaster.sessions.deferred_bohrium import DeferredBohriumSession
from matmaster.types.bohrium_node_runtime import (
    BohriumNodeConnectionInterruptedError,
    BohriumNodeUnavailableError,
)
from matmaster.types.runtime_ports import BohriumNodeBinding
from src.services.bohrium_deferred_runtime import BohriumNodeRuntimeCoordinator


def test_metadata_access_does_not_acquire_node() -> None:
    acquirer = MagicMock()
    session = DeferredBohriumSession(acquirer, workspace_path="/share/case")

    assert session.is_open is True
    assert session.config.workspace_path == "/share/case"
    assert session.remote_skill_roots == []
    assert session.capabilities.file_ops == "sftp"
    acquirer.ensure_ready_sync.assert_not_called()


def test_first_file_access_acquires_and_delegates() -> None:
    ssh = MagicMock()
    ssh.read_file.return_value = "payload"
    binding = BohriumNodeBinding(
        session=ssh,
        execution_workdir="/share/case",
        snapshot=BohriumRuntimeSnapshot(
            session_type="ssh",
            execution_workdir="/share/case",
            node_id=42,
            ssh_attached=True,
        ),
    )
    acquirer = MagicMock()
    acquirer.ensure_ready_sync.return_value = binding
    session = DeferredBohriumSession(acquirer, workspace_path="/share/case")

    assert session.read_file("/share/case/a.txt") == "payload"
    assert session.read_file("/share/case/b.txt") == "payload"

    acquirer.ensure_ready_sync.assert_called_once()
    assert ssh.read_file.call_count == 2


class _DisconnectingSSH:
    def __init__(self, *, recovery_fails: bool) -> None:
        self.connection_generation = 1
        self.recovery_fails = recovery_fails
        self.read_calls = 0
        self.recovery_calls = 0

    def read_file(self, _path: str, *, encoding: str = "utf-8") -> str:
        del encoding
        self.read_calls += 1
        raise ConnectionResetError("transport dropped")

    def is_connection_error(self, error: BaseException) -> bool:
        return isinstance(error, ConnectionError)

    def recover_connection_once(self, *, expected_generation: int) -> None:
        assert expected_generation == 1
        self.recovery_calls += 1
        if self.recovery_fails:
            raise ConnectionError("reconnect failed")
        self.connection_generation += 1


class _InitiallyDisconnectedSSH(_DisconnectingSSH):
    def __init__(self) -> None:
        super().__init__(recovery_fails=False)
        self.connection_active = False

    def read_file(self, _path: str, *, encoding: str = "utf-8") -> str:
        del encoding
        self.read_calls += 1
        return "payload"

    def recover_connection_once(self, *, expected_generation: int) -> None:
        super().recover_connection_once(expected_generation=expected_generation)
        self.connection_active = True


def _deferred_with_real_coordinator(
    ssh: _DisconnectingSSH,
) -> tuple[DeferredBohriumSession, BohriumNodeRuntimeCoordinator]:
    binding = BohriumNodeBinding(
        session=ssh,
        execution_workdir="/share/case",
        snapshot=BohriumRuntimeSnapshot(
            session_type="ssh",
            execution_workdir="/share/case",
            node_id=42,
            ssh_attached=True,
        ),
    )
    coordinator = BohriumNodeRuntimeCoordinator(
        lambda _cancelled, _policy, _idle_timeout: binding
    )
    return (
        DeferredBohriumSession(coordinator, workspace_path="/share/case"),
        coordinator,
    )


def test_connection_failure_recovers_same_binding_without_replaying_operation() -> None:
    ssh = _DisconnectingSSH(recovery_fails=False)
    session, coordinator = _deferred_with_real_coordinator(ssh)

    with pytest.raises(BohriumNodeConnectionInterruptedError):
        session.read_file("/share/case/a.txt")

    assert ssh.read_calls == 1
    assert ssh.recovery_calls == 1
    assert coordinator.unavailable_for_run is False


def test_disconnect_before_operation_recovers_without_an_llm_visible_failure() -> None:
    ssh = _InitiallyDisconnectedSSH()
    session, coordinator = _deferred_with_real_coordinator(ssh)

    assert session.read_file("/share/case/a.txt") == "payload"

    assert ssh.read_calls == 1
    assert ssh.recovery_calls == 1
    assert coordinator.unavailable_for_run is False

    ssh.connection_active = False
    with pytest.raises(BohriumNodeUnavailableError):
        session.read_file("/share/case/b.txt")
    assert ssh.read_calls == 1
    assert ssh.recovery_calls == 1


def test_failed_recovery_opens_circuit_and_fences_direct_session_access() -> None:
    ssh = _DisconnectingSSH(recovery_fails=True)
    session, coordinator = _deferred_with_real_coordinator(ssh)

    with pytest.raises(BohriumNodeUnavailableError):
        session.read_file("/share/case/a.txt")
    with pytest.raises(BohriumNodeUnavailableError):
        session.read_file("/share/case/b.txt")

    assert ssh.read_calls == 1
    assert ssh.recovery_calls == 1
    assert coordinator.unavailable_for_run is True
