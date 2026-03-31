"""Tests for unified Playground core lifecycle.

Each test writes a temporary YAML config, creates a Playground, and verifies
prepare() / cleanup() behavior.  Session classes are patched to avoid
Docker/SSH side effects -- only LocalSession is used with a lightweight stub.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

from matmaster.core.playground import Playground
from matmaster.types.context import PlaygroundContext, WorkspaceArchivalConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, overrides: dict[str, Any] | None = None) -> Path:
    """Write a minimal YAML config and return its path.

    Includes all required sections for ``EvoMasterConfig`` validation
    (env.cluster, env.docker, env.scheduler) so that ConfigManager.load()
    succeeds without any real infrastructure.
    """
    config: dict[str, Any] = {
        "session": {
            "type": "local",
            "local": {
                "workspace_path": "/tmp/ws",
                "timeout": 30,
            },
        },
        "logging": {
            "level": "INFO",
        },
        "env": {
            "cluster": {
                "debug_pool": {"type": "cpu"},
                "train_pool": {"type": "cpu"},
            },
            "docker": {},
            "scheduler": {},
        },
    }
    if overrides:
        config.update(overrides)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    return config_path


# ---------------------------------------------------------------------------
# prepare() returns PlaygroundContext
# ---------------------------------------------------------------------------


class TestPrepare:
    def test_returns_playground_context(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        pg = Playground(config_path=config_path)
        run_dir = tmp_path / "runs" / "run-001"
        run_dir.mkdir(parents=True)

        ctx = pg.prepare({"run_dir": str(run_dir), "task_id": "t1"})

        assert isinstance(ctx, PlaygroundContext)
        assert ctx.session_type == "local"
        pg.cleanup()

    def test_prepare_sets_execution_workdir(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        pg = Playground(config_path=config_path)
        run_dir = tmp_path / "runs" / "run-exec"
        run_dir.mkdir(parents=True)

        ctx = pg.prepare({"run_dir": str(run_dir), "task_id": "t1"})

        assert ctx.execution_workdir == str(ctx.workdir)
        pg.cleanup()

    def test_workspace_created_under_run_dir_with_task_id(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        pg = Playground(config_path=config_path)
        run_dir = tmp_path / "runs" / "run-002"
        run_dir.mkdir(parents=True)

        ctx = pg.prepare({"run_dir": str(run_dir), "task_id": "task-abc"})

        expected_ws = run_dir / "workspaces" / "task-abc"
        assert ctx.workdir == expected_ws
        assert expected_ws.is_dir()
        pg.cleanup()

    def test_workspace_fallback_without_task_id(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        pg = Playground(config_path=config_path)
        run_dir = tmp_path / "runs" / "run-003"
        run_dir.mkdir(parents=True)

        ctx = pg.prepare({"run_dir": str(run_dir)})

        expected_ws = run_dir / "workspace"
        assert ctx.workdir == expected_ws
        assert expected_ws.is_dir()
        pg.cleanup()

    def test_cache_area_created(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        pg = Playground(config_path=config_path)
        run_dir = tmp_path / "runs" / "run-004"
        run_dir.mkdir(parents=True)

        ctx = pg.prepare({"run_dir": str(run_dir), "task_id": "t2"})

        assert ctx.cache_area.is_dir()
        pg.cleanup()

    def test_archival_none_when_not_configured(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        pg = Playground(config_path=config_path)
        run_dir = tmp_path / "runs" / "run-005"
        run_dir.mkdir(parents=True)

        ctx = pg.prepare({"run_dir": str(run_dir)})

        assert ctx.archival is None
        pg.cleanup()

    def test_archival_populated_from_config(self, tmp_path: Path) -> None:
        config_path = _write_config(
            tmp_path,
            overrides={
                "playground": {
                    "archival": {
                        "enabled": True,
                        "oss_bucket": "my-bucket",
                        "oss_prefix": "runs/",
                        "credential_ref": "env:oss",
                    }
                }
            },
        )
        pg = Playground(config_path=config_path)
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
        config_path = _write_config(tmp_path)
        pg = Playground(config_path=config_path)
        run_dir = tmp_path / "runs" / "run-007"
        run_dir.mkdir(parents=True)

        pg.prepare({"run_dir": str(run_dir), "task_id": "t3"})

        log_file = run_dir / "logs" / "t3.log"
        assert log_file.exists()
        pg.cleanup()

    def test_log_file_fallback_name(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        pg = Playground(config_path=config_path)
        run_dir = tmp_path / "runs" / "run-008"
        run_dir.mkdir(parents=True)

        pg.prepare({"run_dir": str(run_dir)})

        log_file = run_dir / "logs" / "playground.log"
        assert log_file.exists()
        pg.cleanup()


# ---------------------------------------------------------------------------
# cleanup() session ownership
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_cleanup_closes_owned_session(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        pg = Playground(config_path=config_path)
        run_dir = tmp_path / "runs" / "run-c1"
        run_dir.mkdir(parents=True)

        pg.prepare({"run_dir": str(run_dir)})

        # The session was created by Playground, so _owns_session should be True
        assert pg._owns_session is True
        assert pg.session is not None

        session_ref = pg.session
        pg.cleanup()

        # After cleanup, session should have been closed
        assert session_ref.is_open is False

    def test_cleanup_does_not_close_injected_session(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        pg = Playground(config_path=config_path)
        run_dir = tmp_path / "runs" / "run-c2"
        run_dir.mkdir(parents=True)

        # Create a mock session to inject
        mock_session = MagicMock()
        mock_session.is_open = True
        mock_session.config = MagicMock()
        mock_session.config.workspace_path = "/workspace"

        pg.prepare(
            {
                "run_dir": str(run_dir),
                "session_override": mock_session,
            }
        )

        # Should not own injected session
        assert pg._owns_session is False

        pg.cleanup()

        # Injected session must NOT be closed by Playground
        mock_session.close.assert_not_called()

    def test_cleanup_releases_log_handler(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        pg = Playground(config_path=config_path)
        run_dir = tmp_path / "runs" / "run-c3"
        run_dir.mkdir(parents=True)

        pg.prepare({"run_dir": str(run_dir)})

        assert pg._log_file_handler is not None

        pg.cleanup()

        assert pg._log_file_handler is None


# ---------------------------------------------------------------------------
# Context immutability
# ---------------------------------------------------------------------------


class TestContextImmutability:
    def test_returned_context_is_frozen(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        pg = Playground(config_path=config_path)
        run_dir = tmp_path / "runs" / "run-f1"
        run_dir.mkdir(parents=True)

        ctx = pg.prepare({"run_dir": str(run_dir)})

        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ctx.workdir = Path("/other")
        pg.cleanup()
