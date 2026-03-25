"""Tests for PlaygroundManager lifecycle management."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from matmaster.core.playground import Playground, PlaygroundManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(tmp_path: Path, overrides: dict[str, Any] | None = None) -> Path:
    """Write a minimal YAML config and return its path."""
    config: dict[str, Any] = {
        "session": {
            "type": "local",
            "local": {"workspace_path": "/tmp/ws", "timeout": 30},
        },
        "logging": {"level": "INFO"},
        "env": {
            "cluster": {"debug_pool": {"type": "cpu"}, "train_pool": {"type": "cpu"}},
            "docker": {},
            "scheduler": {},
        },
        "agents": {"general": {"llm": "default"}},
    }
    if overrides:
        config.update(overrides)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    return config_path


def _setup_project_root(tmp_path: Path) -> Path:
    """Create a fake project root with mat_master and minimal config dirs."""
    for pg_type in ("mat_master", "minimal"):
        cfg_dir = tmp_path / "configs" / pg_type
        cfg_dir.mkdir(parents=True)
        _write_config(cfg_dir)
    return tmp_path


# ---------------------------------------------------------------------------
# validate_startup()
# ---------------------------------------------------------------------------

class TestValidateStartup:
    def test_sets_init_done(self, tmp_path: Path) -> None:
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        assert not mgr._init_done.is_set()
        mgr.validate_startup()
        assert mgr._init_done.is_set()

    def test_idempotent(self, tmp_path: Path) -> None:
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        mgr.validate_startup()
        mgr.validate_startup()  # should not raise
        assert mgr._init_done.is_set()

    def test_warns_missing_config(self, tmp_path: Path) -> None:
        root = tmp_path / "empty_root"
        root.mkdir()
        mgr = PlaygroundManager(root)

        # Should not raise, only log warnings
        mgr.validate_startup()
        assert mgr._init_done.is_set()

    def test_warns_missing_agents_key(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        cfg_dir = root / "configs" / "mat_master"
        cfg_dir.mkdir(parents=True)
        # Config without agents key
        (cfg_dir / "config.yaml").write_text(yaml.dump({"session": {"type": "local"}}))
        mgr = PlaygroundManager(root)

        mgr.validate_startup()
        assert mgr._init_done.is_set()


# ---------------------------------------------------------------------------
# get_or_create()
# ---------------------------------------------------------------------------

class TestGetOrCreate:
    def test_creates_new_playground(self, tmp_path: Path) -> None:
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        pg = mgr.get_or_create("session-1")
        assert isinstance(pg, Playground)

    def test_returns_cached_playground(self, tmp_path: Path) -> None:
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        pg1 = mgr.get_or_create("session-1")
        pg2 = mgr.get_or_create("session-1")
        assert pg1 is pg2

    def test_different_sessions_different_playgrounds(self, tmp_path: Path) -> None:
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        pg1 = mgr.get_or_create("session-1")
        pg2 = mgr.get_or_create("session-2")
        assert pg1 is not pg2

    def test_rejects_x_master(self, tmp_path: Path) -> None:
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        with pytest.raises(ValueError, match="x_master"):
            mgr.get_or_create("session-1", playground_type="x_master")

    def test_invalid_playground_type_raises(self, tmp_path: Path) -> None:
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        with pytest.raises(Exception):
            mgr.get_or_create("session-1", playground_type="nonexistent_type")

    def test_thread_safety(self, tmp_path: Path) -> None:
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        results: dict[str, Playground] = {}
        errors: list[Exception] = []

        def create(sid: str) -> None:
            try:
                results[sid] = mgr.get_or_create(sid)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create, args=(f"s-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(results) == 10


# ---------------------------------------------------------------------------
# release()
# ---------------------------------------------------------------------------

class TestRelease:
    def test_removes_from_cache(self, tmp_path: Path) -> None:
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        mgr.get_or_create("session-1")
        mgr.release("session-1")

        # Next get_or_create should create a new instance
        pg2 = mgr.get_or_create("session-1")
        assert isinstance(pg2, Playground)

    def test_calls_cleanup(self, tmp_path: Path) -> None:
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        pg = mgr.get_or_create("session-1")
        with patch.object(pg, "cleanup") as mock_cleanup:
            mgr.release("session-1")
            mock_cleanup.assert_called_once()

    def test_noop_for_unknown_session(self, tmp_path: Path) -> None:
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        # Should not raise
        mgr.release("nonexistent")
