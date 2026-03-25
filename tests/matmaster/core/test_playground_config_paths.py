"""Config-path compatibility tests for mat_master and minimal.

These integration-style tests use the **real** config files to prove that
the unified ``Playground`` class can be driven by both deployment shapes
without any subclasses.

Offline and deterministic -- no Bohrium, MCP, OSS, or Docker connections.
"""

from __future__ import annotations

from pathlib import Path

from matmaster.core.playground import Playground
from matmaster.types.context import PlaygroundContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Resolve repository root regardless of test file depth (e.g. tests/ vs tests/tests/)."""
    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        if (parent / 'pyproject.toml').is_file():
            return parent
    raise RuntimeError('Could not locate repository root (pyproject.toml not found)')


_PROJECT_ROOT = _repo_root()

MAT_MASTER_CONFIG = _PROJECT_ROOT / 'configs' / 'mat_master' / 'config.yaml'
MINIMAL_CONFIG = _PROJECT_ROOT / 'configs' / 'minimal' / 'config.yaml'


# ---------------------------------------------------------------------------
# mat_master config path
# ---------------------------------------------------------------------------


class TestMatMasterConfigPath:
    """Prove that configs/mat_master/config.yaml drives the unified Playground."""

    def test_mat_master_config_path(self, tmp_path: Path) -> None:
        pg = Playground(MAT_MASTER_CONFIG)

        ctx = pg.prepare({'run_dir': tmp_path / 'runs', 'task_id': 'matmaster-case'})

        try:
            assert isinstance(ctx, PlaygroundContext)
            assert ctx.session_type == 'local'
            assert str(ctx.workdir).endswith('runs/workspaces/matmaster-case')

            # Archival must be populated and enabled
            assert ctx.archival is not None
            assert ctx.archival.enabled is True
            assert ctx.archival.oss_prefix == 'matmaster_evo/chat_workspace'

            # Session config workspace_path must be synchronised with workdir.
            # For local sessions only workspace_path exists (working_dir is
            # a Docker/SSH field); when present, both must match.
            cfg = pg.session.config
            ws_abs = str(ctx.workdir.absolute())
            assert cfg.workspace_path == ws_abs
            if hasattr(cfg, 'working_dir'):
                assert cfg.working_dir == ws_abs
        finally:
            pg.cleanup()


# ---------------------------------------------------------------------------
# minimal config path
# ---------------------------------------------------------------------------


class TestMinimalConfigPath:
    """Prove that configs/minimal/config.yaml drives the unified Playground."""

    def test_minimal_config_path(self, tmp_path: Path) -> None:
        pg = Playground(MINIMAL_CONFIG)

        ctx = pg.prepare({'run_dir': tmp_path / 'runs', 'task_id': 'minimal-case'})

        try:
            assert isinstance(ctx, PlaygroundContext)
            assert ctx.session_type == 'local'
            assert str(ctx.workdir).endswith('runs/workspaces/minimal-case')

            # Archival must be present but disabled
            assert ctx.archival is not None
            assert ctx.archival.enabled is False

            # Session config workspace_path must be synchronised with workdir.
            cfg = pg.session.config
            ws_abs = str(ctx.workdir.absolute())
            assert cfg.workspace_path == ws_abs
            if hasattr(cfg, 'working_dir'):
                assert cfg.working_dir == ws_abs
        finally:
            pg.cleanup()


# ---------------------------------------------------------------------------
# Cache dir from playground.cache_dir config
# ---------------------------------------------------------------------------


class TestCacheDirFromConfig:
    """Verify that playground.cache_dir is respected by the unified Playground."""

    def test_mat_master_cache_dir_from_config(self, tmp_path: Path) -> None:
        """Cache area resolves playground.cache_dir relative to workspace."""
        pg = Playground(MAT_MASTER_CONFIG)
        ctx = pg.prepare({'run_dir': tmp_path / 'runs', 'task_id': 'cache-test'})

        try:
            # playground.cache_dir = ".cache/matmaster" (relative)
            # Resolved under workspace_path
            assert ctx.cache_area.name == 'matmaster'
            assert '.cache' in str(ctx.cache_area)
            assert ctx.cache_area.is_dir()
        finally:
            pg.cleanup()

    def test_minimal_cache_dir_from_config(self, tmp_path: Path) -> None:
        """Minimal config also gets its own cache_dir."""
        pg = Playground(MINIMAL_CONFIG)
        ctx = pg.prepare({'run_dir': tmp_path / 'runs', 'task_id': 'cache-min'})

        try:
            # playground.cache_dir = ".cache/minimal" (relative)
            assert ctx.cache_area.name == 'minimal'
            assert '.cache' in str(ctx.cache_area)
            assert ctx.cache_area.is_dir()
        finally:
            pg.cleanup()
