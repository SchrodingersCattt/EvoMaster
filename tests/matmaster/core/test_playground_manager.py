"""Tests for PlaygroundManager lifecycle management."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

from matmaster.core.playground import Playground, PlaygroundManager
from matmaster.types.context import WorkspaceArchivalConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(cfg_dir: Path, overrides: dict[str, Any] | None = None) -> Path:
    """Write a minimal YAML config and return the config.yaml path."""
    config: dict[str, Any] = {
        "session": {
            "type": "local",
            "local": {"workspace_path": "/tmp/ws", "timeout": 30},
        },
        "agents": {"general": {"llm": "default"}},
    }
    if overrides:
        config.update(overrides)
    config_path = cfg_dir / "config.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    return config_path


def _setup_project_root(tmp_path: Path, overrides: dict[str, Any] | None = None) -> Path:
    """Create a fake project root with config directory."""
    mm_dir = tmp_path / "config"
    mm_dir.mkdir(parents=True)
    _write_config(mm_dir, overrides)
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
        cfg_dir = root / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.yaml").write_text(yaml.dump({"session": {"type": "local"}}))
        mgr = PlaygroundManager(root)

        mgr.validate_startup()
        assert mgr._init_done.is_set()

    def test_no_evomaster_deprecation_warning(self, tmp_path: Path) -> None:
        """validate_startup no longer imports or warns about evomaster."""
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            mgr.validate_startup()
            evo_warnings = [x for x in w if "evomaster" in str(x.message).lower()]
            assert evo_warnings == [], f"Unexpected evomaster warnings: {evo_warnings}"


# ---------------------------------------------------------------------------
# get_or_create()
# ---------------------------------------------------------------------------


class TestGetOrCreate:
    def test_creates_new_playground(self, tmp_path: Path) -> None:
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        pg = mgr.get_or_create("session-1")
        assert isinstance(pg, Playground)

    def test_parameterized_construction(self, tmp_path: Path) -> None:
        """Playground created by manager has params from YAML config."""
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        pg = mgr.get_or_create("session-1")
        assert pg._session_type == "local"
        assert isinstance(pg._session_config, dict)

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

    def test_archival_from_config(self, tmp_path: Path) -> None:
        root = _setup_project_root(
            tmp_path,
            overrides={
                "playground": {
                    "archival": {
                        "enabled": True,
                        "oss_bucket": "test-bucket",
                        "oss_prefix": "prefix/",
                    }
                }
            },
        )
        mgr = PlaygroundManager(root)

        pg = mgr.get_or_create("session-1")
        assert pg._archival is not None
        assert isinstance(pg._archival, WorkspaceArchivalConfig)
        assert pg._archival.enabled is True
        assert pg._archival.oss_bucket == "test-bucket"

    def test_cache_dir_from_config(self, tmp_path: Path) -> None:
        root = _setup_project_root(
            tmp_path,
            overrides={"playground": {"cache_dir": ".cache/test"}},
        )
        mgr = PlaygroundManager(root)

        pg = mgr.get_or_create("session-1")
        assert pg._cache_dir == ".cache/test"

    def test_workspace_base_from_config(self, tmp_path: Path) -> None:
        root = _setup_project_root(
            tmp_path,
            overrides={"workspace": "./my_workspace"},
        )
        mgr = PlaygroundManager(root)

        pg = mgr.get_or_create("session-1")
        assert pg._workspace_base == "./my_workspace"

    def test_missing_config_uses_defaults(self, tmp_path: Path) -> None:
        root = tmp_path / "no_config"
        root.mkdir()
        (root / "config").mkdir()
        # No config.yaml
        mgr = PlaygroundManager(root)

        pg = mgr.get_or_create("session-1")
        assert pg._session_type == "local"
        assert pg._session_config == {}

    def test_thread_safety_different_sessions(self, tmp_path: Path) -> None:
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        results: list[tuple[str, Playground]] = []
        results_lock = threading.Lock()
        errors: list[Exception] = []

        def create(sid: str) -> None:
            try:
                pg = mgr.get_or_create(sid)
                with results_lock:
                    results.append((sid, pg))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create, args=(f"s-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(results) == 10

    def test_thread_safety_same_session(self, tmp_path: Path) -> None:
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        results: list[Playground] = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(10)

        def create() -> None:
            barrier.wait()
            pg = mgr.get_or_create("same-session")
            with results_lock:
                results.append(pg)

        threads = [threading.Thread(target=create) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 10
        assert all(pg is results[0] for pg in results)


# ---------------------------------------------------------------------------
# _load_raw_config / _build_archival
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_load_raw_config_returns_dict(self, tmp_path: Path) -> None:
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        raw = mgr._load_raw_config()
        assert isinstance(raw, dict)
        assert "session" in raw

    def test_load_raw_config_missing_file(self, tmp_path: Path) -> None:
        root = tmp_path / "empty"
        root.mkdir()
        (root / "config").mkdir()
        mgr = PlaygroundManager(root)

        raw = mgr._load_raw_config()
        assert raw == {}

    def test_build_archival_with_block(self, tmp_path: Path) -> None:
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        archival = mgr._build_archival(
            {"archival": {"enabled": True, "oss_bucket": "b"}}
        )
        assert archival is not None
        assert archival.enabled is True

    def test_build_archival_without_block(self, tmp_path: Path) -> None:
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        archival = mgr._build_archival({})
        assert archival is None


# ---------------------------------------------------------------------------
# release()
# ---------------------------------------------------------------------------


class TestRelease:
    def test_removes_from_cache(self, tmp_path: Path) -> None:
        root = _setup_project_root(tmp_path)
        mgr = PlaygroundManager(root)

        pg1 = mgr.get_or_create("session-1")
        mgr.release("session-1")

        pg2 = mgr.get_or_create("session-1")
        assert isinstance(pg2, Playground)
        assert pg1 is not pg2

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

        mgr.release("nonexistent")
