"""Config-path compatibility tests for mat_master and minimal.

These integration-style tests use the **real** config files to prove that
PlaygroundManager can parse both deployment shapes (mat_master / minimal)
and construct parameterized Playground instances that produce correct
PlaygroundContext snapshots.

Offline and deterministic -- no Bohrium, MCP, OSS, or Docker connections.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from matmaster.core.playground import Playground, PlaygroundManager
from matmaster.types.context import PlaygroundContext, WorkspaceArchivalConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Resolve repository root regardless of test file depth."""
    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        if (parent / 'pyproject.toml').is_file():
            return parent
    raise RuntimeError('Could not locate repository root (pyproject.toml not found)')


_PROJECT_ROOT = _repo_root()

# Real config files for integration tests
MAT_MASTER_CONFIG = _PROJECT_ROOT / 'configs' / 'mat_master' / 'config.yaml'
MINIMAL_CONFIG = _PROJECT_ROOT / 'configs' / 'minimal' / 'config.yaml'


def _playground_from_yaml(config_path: Path) -> Playground:
    """Parse a real config YAML and construct a parameterized Playground.

    Mirrors what PlaygroundManager.get_or_create does internally.
    """
    with open(config_path, encoding='utf-8') as f:
        raw = yaml.safe_load(f) or {}

    session_block = raw.get('session', {})
    if not isinstance(session_block, dict):
        session_block = {}
    session_type = session_block.get('type', 'local')
    session_config = session_block.get(session_type, {})
    if not isinstance(session_config, dict):
        session_config = {}

    playground_block = raw.get('playground', {})
    if not isinstance(playground_block, dict):
        playground_block = {}

    archival_block = playground_block.get('archival')
    archival = None
    if isinstance(archival_block, dict):
        archival = WorkspaceArchivalConfig(**archival_block)

    return Playground(
        session_type=session_type,
        session_config=session_config,
        archival=archival,
        workspace_base=raw.get('workspace'),
        cache_dir=playground_block.get('cache_dir'),
    )


# ---------------------------------------------------------------------------
# mat_master config path
# ---------------------------------------------------------------------------


class TestMatMasterConfigPath:
    """Prove that configs/mat_master/config.yaml drives the parameterized Playground."""

    def test_mat_master_config_path(self, tmp_path: Path) -> None:
        pg = _playground_from_yaml(MAT_MASTER_CONFIG)

        ctx = pg.prepare({'run_dir': tmp_path / 'runs', 'task_id': 'matmaster-case'})

        try:
            assert isinstance(ctx, PlaygroundContext)
            assert ctx.session_type == 'local'
            assert str(ctx.workdir).endswith('runs/workspaces/matmaster-case')

            # Archival must be populated and enabled
            assert ctx.archival is not None
            assert ctx.archival.enabled is True
            assert ctx.archival.oss_prefix == 'matmaster_evo/chat_workspace'
        finally:
            pg.cleanup()


# ---------------------------------------------------------------------------
# minimal config path
# ---------------------------------------------------------------------------


class TestMinimalConfigPath:
    """Prove that configs/minimal/config.yaml drives the parameterized Playground."""

    def test_minimal_config_path(self, tmp_path: Path) -> None:
        pg = _playground_from_yaml(MINIMAL_CONFIG)

        ctx = pg.prepare({'run_dir': tmp_path / 'runs', 'task_id': 'minimal-case'})

        try:
            assert isinstance(ctx, PlaygroundContext)
            assert ctx.session_type == 'local'
            assert str(ctx.workdir).endswith('runs/workspaces/minimal-case')

            # Archival must be present but disabled
            assert ctx.archival is not None
            assert ctx.archival.enabled is False
        finally:
            pg.cleanup()


# ---------------------------------------------------------------------------
# Cache dir from playground.cache_dir config
# ---------------------------------------------------------------------------


class TestCacheDirFromConfig:
    """Verify that playground.cache_dir is respected by the parameterized Playground."""

    def test_mat_master_cache_dir_from_config(self, tmp_path: Path) -> None:
        """Cache area resolves playground.cache_dir relative to workspace."""
        pg = _playground_from_yaml(MAT_MASTER_CONFIG)
        ctx = pg.prepare({'run_dir': tmp_path / 'runs', 'task_id': 'cache-test'})

        try:
            # playground.cache_dir = ".cache/matmaster" (relative)
            assert ctx.cache_area.name == 'matmaster'
            assert '.cache' in str(ctx.cache_area)
            assert ctx.cache_area.is_dir()
        finally:
            pg.cleanup()

    def test_minimal_cache_dir_from_config(self, tmp_path: Path) -> None:
        """Minimal config also gets its own cache_dir."""
        pg = _playground_from_yaml(MINIMAL_CONFIG)
        ctx = pg.prepare({'run_dir': tmp_path / 'runs', 'task_id': 'cache-min'})

        try:
            # playground.cache_dir = ".cache/minimal" (relative)
            assert ctx.cache_area.name == 'minimal'
            assert '.cache' in str(ctx.cache_area)
            assert ctx.cache_area.is_dir()
        finally:
            pg.cleanup()
