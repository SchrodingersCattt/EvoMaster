"""Cleanup and rollback contracts for Bohrium execution binding."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests.matmaster.integration.test_bohrium_execution_contract import (
    SESSIONS,
    _make_bohrium_service,
    _make_pg,
    arb,
)


@pytest.fixture(autouse=True)
def _clear_sessions() -> None:
    SESSIONS.clear()
    yield
    SESSIONS.clear()


def test_cleanup_destroys_created_node_when_reuse_table_insert_fails() -> None:
    node_svc = MagicMock()
    nodes_table = MagicMock()
    nodes_table.find_one_for_reuse.return_value = None
    nodes_table.list_node_ids_for_user_org.return_value = []
    nodes_table.insert_node.side_effect = RuntimeError("db down")

    node_svc.create_node.return_value = {"node_id": 42}
    node_svc.wait_until_ready.return_value = {
        "ip": "10.0.0.1",
        "password": "secret",
    }

    original_session = MagicMock()
    original_session.is_open = True
    pg = _make_pg(original_session)
    mock_ssh = MagicMock()
    mock_ssh.is_open = True

    sessions_service = MagicMock()
    sessions_service.get_session.return_value = {
        "user_id": "u1",
        "org_id": "o1",
        "project_id": 99,
    }

    with (
        patch.object(arb, "SSHSession", return_value=mock_ssh),
        patch.object(arb, "_run_clear_remote_proxy", MagicMock()),
        patch.object(arb, "_remote_session_workspace_root", return_value="/share"),
        patch(
            "src.services.agent_run_bohrium.get_bohrium_node_service",
            return_value=node_svc,
        ),
        patch(
            "src.services.agent_run_bohrium.get_bohrium_nodes_table",
            return_value=nodes_table,
        ),
        patch(
            "src.services.agent_run_bohrium.UserService.get_bohrium_access_key",
            return_value="ak",
        ),
    ):
        svc = _make_bohrium_service(sessions_service)
        result = svc._setup_bohrium_for_run(
            session_id="sess-untracked",
            pg=pg,
            run_creds={
                "access_key": "ak",
                "project_id": 99,
            },
            user_id_for_ak="u1",
            org_id="o1",
            event_callback=MagicMock(),
            run_started_at=0.0,
            bohrium_node_sku_id=12345,
        )

        assert result.ssh_attached is True
        assert SESSIONS["sess-untracked"]["bohrium_node_reuse_tracked"] is False

        svc._cleanup_bohrium_after_run(
            session_id="sess-untracked",
            event_callback=MagicMock(),
            pg_for_run=pg,
            ssh_attached=True,
        )

    nodes_table.update_last_used_at.assert_not_called()
    node_svc.destroy_node.assert_called_once_with(
        "ak",
        42,
        99,
        creator_id=arb._creator_id_from_user("u1"),
    )


@patch.object(arb, "_run_clear_remote_proxy", MagicMock())
@patch.object(arb, "_remote_session_workspace_root", return_value="/share")
@patch("src.services.agent_run_bohrium.get_bohrium_nodes_table")
@patch("src.services.agent_run_bohrium.get_bohrium_node_service")
def test_setup_does_not_emit_skills_synced_event(
    mock_node_svc_factory: MagicMock,
    mock_nodes_table_factory: MagicMock,
    mock_remote_workspace_root: MagicMock,
) -> None:
    """Bohrium setup no longer owns skill directory sync telemetry."""
    node_svc = MagicMock()
    mock_node_svc_factory.return_value = node_svc
    nodes_table = MagicMock()
    mock_nodes_table_factory.return_value = nodes_table
    nodes_table.find_one_for_reuse.return_value = None
    nodes_table.list_node_ids_for_user_org.return_value = []

    node_svc.create_node.return_value = {"node_id": 42}
    node_svc.wait_until_ready.return_value = {
        "ip": "10.0.0.1",
        "password": "secret",
    }

    original_session = MagicMock()
    original_session.is_open = True
    pg = _make_pg(original_session)
    event_callback = MagicMock()

    class FakeSSHSession:
        def __init__(self, config: Any) -> None:
            self.config = config
            self.is_open = False

        def open(self) -> None:
            self.is_open = True

        def close(self) -> None:
            self.is_open = False

    with patch.object(arb, "SSHSession", new=FakeSSHSession):
        svc = _make_bohrium_service()
        result = svc._setup_bohrium_for_run(
            session_id="sess-no-skill-sync",
            pg=pg,
            run_creds={
                "access_key": "ak",
                "project_id": 99,
            },
            user_id_for_ak="u1",
            org_id="o1",
            event_callback=event_callback,
            run_started_at=0.0,
        )

    assert result.ssh_attached is True
    assert not any(
        call.args[1] == "bohrium_node"
        and isinstance(call.args[2], dict)
        and call.args[2].get("status") == "skills_synced"
        for call in event_callback.call_args_list
    )


@patch.object(arb, "_run_clear_remote_proxy")
@patch.object(arb, "_remote_session_workspace_root", return_value="/share")
@patch("src.services.agent_run_bohrium.get_bohrium_nodes_table")
@patch("src.services.agent_run_bohrium.get_bohrium_node_service")
def test_setup_failure_after_open_restores_original_and_clears_runtime(
    mock_node_svc_factory: MagicMock,
    mock_nodes_table_factory: MagicMock,
    mock_remote_workspace_root: MagicMock,
    mock_run_clear_remote_proxy: MagicMock,
) -> None:
    """If setup fails after swap/store, restore the original playground session."""
    node_svc = MagicMock()
    mock_node_svc_factory.return_value = node_svc
    nodes_table = MagicMock()
    mock_nodes_table_factory.return_value = nodes_table
    nodes_table.find_one_for_reuse.return_value = None
    nodes_table.list_node_ids_for_user_org.return_value = []

    node_svc.create_node.return_value = {"node_id": 42}
    node_svc.wait_until_ready.return_value = {
        "ip": "10.0.0.1",
        "password": "secret",
    }

    original_session = MagicMock()
    original_session.is_open = True
    pg = _make_pg(original_session)

    mock_ssh = MagicMock()
    mock_ssh.is_open = True

    def _raise_after_store(pg_obj: object, phase: str) -> None:
        if phase == "post_ssh":
            raise RuntimeError("post-store failure")

    with patch.object(arb, "SSHSession", return_value=mock_ssh):
        mock_run_clear_remote_proxy.side_effect = _raise_after_store
        event_callback = MagicMock()
        svc = _make_bohrium_service()
        result = svc._setup_bohrium_for_run(
            session_id="sess-fail",
            pg=pg,
            run_creds={
                "access_key": "ak",
                "project_id": 99,
            },
            user_id_for_ak="u1",
            org_id="o1",
            event_callback=event_callback,
            run_started_at=0.0,
        )

    assert result.ssh_attached is False
    assert result.abort_result is not None
    assert pg.session is original_session
    assert pg._owns_session is True
    assert "bohrium_runtime" not in SESSIONS.get("sess-fail", {})
    mock_ssh.open.assert_called_once()
    mock_ssh.close.assert_called_once()
    mock_run_clear_remote_proxy.assert_called_once_with(pg, "post_ssh")
    event_callback.assert_any_call(
        "System",
        "bohrium_node",
        {
            "status": "failed",
            "message": "Bohrium 节点创建失败: post-store failure",
            "node_id": 42,
        },
    )


@patch("src.services.agent_run_bohrium.get_bohrium_nodes_table")
@patch("src.services.agent_run_bohrium.get_bohrium_node_service")
def test_cleanup_restores_when_ssh_attached_false(
    _mock_node_svc: MagicMock,
    _mock_nodes_table: MagicMock,
) -> None:
    """cleanup_bohrium_after_run restores session/_owns_session from runtime when ssh_attached=False."""
    original_session = MagicMock()
    original_session.is_open = True
    ssh_session = MagicMock()
    ssh_session.is_open = True

    pg = SimpleNamespace(session=ssh_session, _owns_session=False)

    SESSIONS["sess-x"] = {
        "bohrium_runtime": {
            "original_session": original_session,
            "original_owns_session": True,
            "ssh_session": ssh_session,
        },
        "bohrium_node_id": None,
    }

    sessions_service = MagicMock()
    sessions_service.get_session.return_value = None
    sessions_service.get_session_user_id.return_value = None

    svc = _make_bohrium_service(sessions_service)
    svc._cleanup_bohrium_after_run(
        session_id="sess-x",
        event_callback=MagicMock(),
        pg_for_run=pg,
        ssh_attached=False,
    )

    assert pg.session is original_session
    assert pg._owns_session is True
    ssh_session.close.assert_called_once()
    assert "bohrium_runtime" not in SESSIONS["sess-x"]


def test_lease_cleanup_does_not_depend_on_session_lookup() -> None:
    heartbeat = MagicMock()
    manager = MagicMock()
    lease = MagicMock(invocation_id="inv-1", node_id=42)
    SESSIONS["sess-lease-error"] = {
        "bohrium_node_lease_runtimes": {
            "inv-1": {
                "heartbeat": heartbeat,
                "lease": lease,
                "manager": manager,
                "access_key": "ak",
                "creator_id": 1,
            }
        }
    }
    sessions_service = MagicMock()
    sessions_service.get_session.side_effect = RuntimeError("db unavailable")
    svc = _make_bohrium_service(sessions_service)

    svc._cleanup_bohrium_after_run(
        session_id="sess-lease-error",
        event_callback=MagicMock(),
        pg_for_run=None,
        ssh_attached=False,
        invocation_id="inv-1",
    )

    heartbeat.stop.assert_called_once_with()
    manager.release.assert_called_once_with(lease, access_key="ak", creator_id=1)
    sessions_service.get_session.assert_not_called()


def test_lease_cleanup_only_releases_current_invocation() -> None:
    heartbeat_1 = MagicMock()
    heartbeat_2 = MagicMock()
    manager = MagicMock()
    lease_1 = MagicMock(invocation_id="inv-1", node_id=41)
    lease_2 = MagicMock(invocation_id="inv-2", node_id=42)
    runtime_2 = {
        "heartbeat": heartbeat_2,
        "lease": lease_2,
        "manager": manager,
        "access_key": "ak",
        "creator_id": 1,
    }
    SESSIONS["sess-concurrent-leases"] = {
        "bohrium_node_lease_runtimes": {
            "inv-1": {
                "heartbeat": heartbeat_1,
                "lease": lease_1,
                "manager": manager,
                "access_key": "ak",
                "creator_id": 1,
            },
            "inv-2": runtime_2,
        }
    }
    sessions_service = MagicMock()
    svc = _make_bohrium_service(sessions_service)

    svc._cleanup_bohrium_after_run(
        session_id="sess-concurrent-leases",
        event_callback=MagicMock(),
        pg_for_run=None,
        ssh_attached=False,
        invocation_id="inv-1",
    )

    heartbeat_1.stop.assert_called_once_with()
    heartbeat_2.stop.assert_not_called()
    manager.release.assert_called_once_with(lease_1, access_key="ak", creator_id=1)
    assert SESSIONS["sess-concurrent-leases"]["bohrium_node_lease_runtimes"] == {
        "inv-2": runtime_2
    }
    sessions_service.get_session.assert_not_called()
