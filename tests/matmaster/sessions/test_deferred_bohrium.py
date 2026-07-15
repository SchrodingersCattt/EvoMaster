from __future__ import annotations

from unittest.mock import MagicMock

from matmaster.bohrium.types import BohriumRuntimeSnapshot
from matmaster.sessions.deferred_bohrium import DeferredBohriumSession
from matmaster.types.runtime_ports import BohriumNodeBinding


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
