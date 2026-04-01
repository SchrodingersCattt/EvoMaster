"""Tests for unified Playground core lifecycle.

Each test creates a Playground with parameterized construction, and verifies
prepare() / cleanup() behavior.  Only LocalSession is used directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from matmaster.core.playground import Playground
from matmaster.sessions.local import LocalSession
from matmaster.types.context import PlaygroundContext, WorkspaceArchivalConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_playground(
    tmp_path: Path,
    *,
    session_type: str = "local",
    session_config: dict[str, Any] | None = None,
    archival: WorkspaceArchivalConfig | None = None,
    workspace_base: str | None = None,
    cache_dir: str | None = None,
) -> Playground:
    """Create a Playground with sensible defaults for testing."""
    if session_config is None:
        session_config = {"workspace_path": str(tmp_path / "ws"), "timeout": 30}
    return Playground(
        session_type=session_type,
        session_config=session_config,
        archival=archival,
        workspace_base=workspace_base,
        cache_dir=cache_dir,
    )


# ---------------------------------------------------------------------------
# prepare() returns PlaygroundContext
# ---------------------------------------------------------------------------


class TestPrepare:
    def test_returns_playground_context(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        run_dir = tmp_path / "runs" / "run-001"
        run_dir.mkdir(parents=True)

        ctx = pg.prepare({"run_dir": str(run_dir), "task_id": "t1"})

        assert isinstance(ctx, PlaygroundContext)
        assert ctx.session_type == "local"
        pg.cleanup()

    def test_prepare_sets_execution_workdir(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        run_dir = tmp_path / "runs" / "run-exec"
        run_dir.mkdir(parents=True)

        ctx = pg.prepare({"run_dir": str(run_dir), "task_id": "t1"})

        assert ctx.execution_workdir == str(ctx.workdir)
        pg.cleanup()

    def test_workspace_created_under_run_dir_with_task_id(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        run_dir = tmp_path / "runs" / "run-002"
        run_dir.mkdir(parents=True)

        ctx = pg.prepare({"run_dir": str(run_dir), "task_id": "task-abc"})

        expected_ws = run_dir / "workspaces" / "task-abc"
        assert ctx.workdir == expected_ws
        assert expected_ws.is_dir()
        pg.cleanup()

    def test_workspace_fallback_without_task_id(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        run_dir = tmp_path / "runs" / "run-003"
        run_dir.mkdir(parents=True)

        ctx = pg.prepare({"run_dir": str(run_dir)})

        expected_ws = run_dir / "workspace"
        assert ctx.workdir == expected_ws
        assert expected_ws.is_dir()
        pg.cleanup()

    def test_workspace_fallback_without_run_dir(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path, workspace_base=str(tmp_path / "base"))

        ctx = pg.prepare({})

        expected_ws = tmp_path / "base" / "default"
        assert ctx.workdir == expected_ws
        assert expected_ws.is_dir()
        pg.cleanup()

    def test_cache_area_created(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        run_dir = tmp_path / "runs" / "run-004"
        run_dir.mkdir(parents=True)

        ctx = pg.prepare({"run_dir": str(run_dir), "task_id": "t2"})

        assert ctx.cache_area.is_dir()
        pg.cleanup()

    def test_archival_none_when_not_configured(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        run_dir = tmp_path / "runs" / "run-005"
        run_dir.mkdir(parents=True)

        ctx = pg.prepare({"run_dir": str(run_dir)})

        assert ctx.archival is None
        pg.cleanup()

    def test_archival_populated_from_params(self, tmp_path: Path) -> None:
        archival = WorkspaceArchivalConfig(
            enabled=True,
            oss_bucket="my-bucket",
            oss_prefix="runs/",
            credential_ref="env:oss",
        )
        pg = _make_playground(tmp_path, archival=archival)
        run_dir = tmp_path / "runs" / "run-006"
        run_dir.mkdir(parents=True)

        ctx = pg.prepare({"run_dir": str(run_dir)})

        assert ctx.archival is not None
        assert isinstance(ctx.archival, WorkspaceArchivalConfig)
        assert ctx.archival.enabled is True
        assert ctx.archival.oss_bucket == "my-bucket"
        assert ctx.archival.oss_prefix == "runs/"
        assert ctx.archival.credential_ref == "env:oss"
        pg.cleanup()

    def test_log_file_created(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        run_dir = tmp_path / "runs" / "run-007"
        run_dir.mkdir(parents=True)

        pg.prepare({"run_dir": str(run_dir), "task_id": "t3"})

        log_file = run_dir / "logs" / "t3.log"
        assert log_file.exists()
        pg.cleanup()

    def test_log_file_fallback_name(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        run_dir = tmp_path / "runs" / "run-008"
        run_dir.mkdir(parents=True)

        pg.prepare({"run_dir": str(run_dir)})

        log_file = run_dir / "logs" / "playground.log"
        assert log_file.exists()
        pg.cleanup()

    def test_custom_cache_dir_relative(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path, cache_dir=".cache/custom")
        run_dir = tmp_path / "runs" / "run-cache"
        run_dir.mkdir(parents=True)

        ctx = pg.prepare({"run_dir": str(run_dir), "task_id": "t1"})

        assert ctx.cache_area.name == "custom"
        assert ".cache" in str(ctx.cache_area)
        assert ctx.cache_area.is_dir()
        pg.cleanup()


# ---------------------------------------------------------------------------
# cleanup() session ownership
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_cleanup_closes_owned_session(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        run_dir = tmp_path / "runs" / "run-c1"
        run_dir.mkdir(parents=True)

        pg.prepare({"run_dir": str(run_dir)})

        assert pg._owns_session is True
        assert pg.session is not None

        session_ref = pg.session
        pg.cleanup()

        assert session_ref.is_open is False

    def test_cleanup_does_not_close_injected_session(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        run_dir = tmp_path / "runs" / "run-c2"
        run_dir.mkdir(parents=True)

        # MagicMock needs spec to satisfy Session Protocol isinstance check
        mock_session = MagicMock(spec=LocalSession)
        mock_session.is_open = True

        pg.prepare(
            {
                "run_dir": str(run_dir),
                "session_override": mock_session,
            }
        )

        assert pg._owns_session is False

        pg.cleanup()

        mock_session.close.assert_not_called()

    def test_cleanup_releases_log_handler(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        run_dir = tmp_path / "runs" / "run-c3"
        run_dir.mkdir(parents=True)

        pg.prepare({"run_dir": str(run_dir)})

        assert pg._log_file_handler is not None

        pg.cleanup()

        assert pg._log_file_handler is None


# ---------------------------------------------------------------------------
# _create_session_from_config
# ---------------------------------------------------------------------------


class TestCreateSession:
    def test_local_session_created(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        session = pg._create_session_from_config()

        assert isinstance(session, LocalSession)

    def test_ssh_session_created(self, tmp_path: Path) -> None:
        pg = _make_playground(
            tmp_path,
            session_type="ssh",
            session_config={
                "host": "example.com",
                "port": 22,
                "username": "root",
                "password": "secret",
            },
        )
        with patch("matmaster.sessions.ssh.SSHSession") as mock_cls:
            mock_cls.return_value = MagicMock()
            session = pg._create_session_from_config()
            mock_cls.assert_called_once()

    def test_docker_raises_value_error(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path, session_type="docker")
        with pytest.raises(ValueError, match="Unsupported session type"):
            pg._create_session_from_config()


# ---------------------------------------------------------------------------
# Session management (inlined from Mixin)
# ---------------------------------------------------------------------------


class TestSessionManagement:
    def test_attach_session(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        mock_session = MagicMock()
        mock_session.is_open = False

        pg.attach_session(mock_session)

        assert pg.session is mock_session
        assert pg._owns_session is True
        mock_session.open.assert_called_once()

    def test_attach_session_closes_previous_non_local(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        old_session = MagicMock()
        old_session.is_open = True
        pg.session = old_session

        new_session = MagicMock()
        new_session.is_open = False

        pg.attach_session(new_session)

        old_session.close.assert_called_once()
        assert pg.session is new_session

    def test_attach_session_propagates_to_agent(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        mock_agent = MagicMock()
        pg.agent = mock_agent

        mock_session = MagicMock()
        mock_session.is_open = True

        pg.attach_session(mock_session)

        assert mock_agent.session is mock_session

    def test_detach_session(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        mock_session = MagicMock()
        mock_session.is_open = True
        pg.session = mock_session

        pg.detach_session()

        assert pg.session is None
        mock_session.close.assert_called_once()

    def test_detach_session_clears_agent_ref(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        mock_agent = MagicMock()
        pg.agent = mock_agent
        mock_session = MagicMock()
        mock_session.is_open = True
        pg.session = mock_session

        pg.detach_session()

        assert mock_agent.session is None

    def test_detach_session_skips_local(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        local_session = LocalSession(workspace_path=tmp_path)
        local_session.open()
        pg.session = local_session

        pg.detach_session()

        assert pg.session is None
        # LocalSession close should NOT be called by detach for local sessions
        assert local_session.is_open is True

    def test_attach_ssh_session(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        with patch("matmaster.sessions.ssh.SSHSession") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.is_open = False
            mock_cls.return_value = mock_instance

            result = pg.attach_ssh_session(
                host="example.com",
                port=22,
                username="root",
                password="secret",
            )

            mock_cls.assert_called_once()
            assert result is mock_instance
            assert pg.session is mock_instance


# ---------------------------------------------------------------------------
# Context immutability
# ---------------------------------------------------------------------------


class TestContextImmutability:
    def test_returned_context_is_frozen(self, tmp_path: Path) -> None:
        pg = _make_playground(tmp_path)
        run_dir = tmp_path / "runs" / "run-f1"
        run_dir.mkdir(parents=True)

        ctx = pg.prepare({"run_dir": str(run_dir)})

        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ctx.workdir = Path("/other")
        pg.cleanup()


# ---------------------------------------------------------------------------
# Session/ownership attributes are directly writable (pg.session = ...)
# ---------------------------------------------------------------------------


class TestDirectWritableAttributes:
    def test_session_directly_writable(self, tmp_path: Path) -> None:
        """agent_run_bohrium.py does pg.session = ssh_session directly."""
        pg = _make_playground(tmp_path)
        mock_session = MagicMock()
        pg.session = mock_session
        assert pg.session is mock_session

    def test_owns_session_directly_writable(self, tmp_path: Path) -> None:
        """agent_run_bohrium.py does pg._owns_session = False directly."""
        pg = _make_playground(tmp_path)
        pg._owns_session = False
        assert pg._owns_session is False
