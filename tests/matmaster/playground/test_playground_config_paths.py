"""Config-path compatibility tests for mat_master and minimal.

These integration-style tests use the **real** config files to prove that
the unified ``Playground`` class can be driven by both deployment shapes
without any subclasses.

Offline and deterministic -- no Bohrium, MCP, OSS, or Docker connections.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from matmaster.playground import Playground
from matmaster.types.context import PlaygroundContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # repo root

MAT_MASTER_CONFIG = _PROJECT_ROOT / "configs" / "mat_master" / "config.yaml"
MINIMAL_CONFIG = _PROJECT_ROOT / "configs" / "minimal" / "config.yaml"


# ---------------------------------------------------------------------------
# mat_master config path
# ---------------------------------------------------------------------------


class TestMatMasterConfigPath:
    def test_mat_master_config_path(self, tmp_path: Path) -> None:
        pg = Playground(MAT_MASTER_CONFIG)

        ctx = pg.prepare({"run_dir": tmp_path / "runs", "task_id": "matmaster-case"})

        try:
            assert isinstance(ctx, PlaygroundContext)
            assert ctx.session_type == "local"
            assert str(ctx.workdir).endswith("runs/workspaces/matmaster-case")

            # Archival enabled for mat_master
            assert ctx.archival is not None
            assert ctx.archival.enabled is True
            assert ctx.archival.oss_prefix == "matmaster_evo/chat_workspace"

            # Session config sync: workspace_path == working_dir
            cfg = pg.session.config
            if hasattr(cfg, "workspace_path") and hasattr(cfg, "working_dir"):
                assert cfg.workspace_path == cfg.working_dir
        finally:
            pg.cleanup()


# ---------------------------------------------------------------------------
# minimal config path
# ---------------------------------------------------------------------------


class TestMinimalConfigPath:
    def test_minimal_config_path(self, tmp_path: Path) -> None:
        pg = Playground(MINIMAL_CONFIG)

        ctx = pg.prepare({"run_dir": tmp_path / "runs", "task_id": "minimal-case"})

        try:
            assert isinstance(ctx, PlaygroundContext)
            assert ctx.session_type == "local"
            assert str(ctx.workdir).endswith("runs/workspaces/minimal-case")

            # Archival disabled for minimal
            assert ctx.archival is not None
            assert ctx.archival.enabled is False

            # Session config sync: workspace_path == working_dir
            cfg = pg.session.config
            if hasattr(cfg, "workspace_path") and hasattr(cfg, "working_dir"):
                assert cfg.workspace_path == cfg.working_dir
        finally:
            pg.cleanup()


# ---------------------------------------------------------------------------
# Cache dir from playground.cache_dir config
# ---------------------------------------------------------------------------


class TestCacheDirFromConfig:
    def test_mat_master_cache_dir_from_config(self, tmp_path: Path) -> None:
        """Cache area should respect playground.cache_dir when configured."""
        pg = Playground(MAT_MASTER_CONFIG)
        ctx = pg.prepare({"run_dir": tmp_path / "runs", "task_id": "cache-test"})

        try:
            # playground.cache_dir = ".cache/matmaster" (relative)
            # Should resolve under workspace path
            assert ctx.cache_area.name == "matmaster" or ".cache" in str(ctx.cache_area)
            assert ctx.cache_area.is_dir()
        finally:
            pg.cleanup()
