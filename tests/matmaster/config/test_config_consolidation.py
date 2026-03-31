"""Validate cleaned config.yaml loads through EvoMasterConfig without errors."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def cleaned_config():
    config_path = Path("matmaster_config/config.yaml")
    if not config_path.exists():
        pytest.skip("matmaster_config/config.yaml not found")
    with open(config_path) as f:
        return yaml.safe_load(f)


class TestCleanedConfigYaml:
    def test_loads_via_evomaster_config(self, cleaned_config):
        """EvoMasterConfig(**config_dict) must not raise."""
        from evomaster.config import EvoMasterConfig

        cfg = EvoMasterConfig(**cleaned_config)
        assert cfg.env is not None  # env stub loaded

    def test_has_agents_general_llm(self, cleaned_config):
        assert cleaned_config["agents"]["general"]["llm"] == "opus"

    def test_no_dead_sections(self, cleaned_config):
        dead = {
            "llm",
            "mat_master",
            "llm_output",
            "logging",
            "skills",
            "project_root",
            "results_dir",
            "debug",
            "mcp",
        }
        present_dead = dead & set(cleaned_config.keys())
        assert present_dead == set(), f"Dead sections still present: {present_dead}"

    def test_env_stub_present(self, cleaned_config):
        assert "env" in cleaned_config
        assert "cluster" in cleaned_config["env"]
        assert "docker" in cleaned_config["env"]
        assert "scheduler" in cleaned_config["env"]

    def test_session_present(self, cleaned_config):
        assert "session" in cleaned_config
        assert cleaned_config["session"]["type"] == "local"

    def test_playground_present(self, cleaned_config):
        assert "playground" in cleaned_config
        assert "archival" in cleaned_config["playground"]

    def test_workspace_present(self, cleaned_config):
        assert "workspace" in cleaned_config


class TestConfigDirRouting:
    def test_config_dir_is_matmaster_config(self, tmp_path):
        from matmaster.core.playground import PlaygroundManager

        mgr = PlaygroundManager(tmp_path)
        assert mgr._config_dir == tmp_path / "matmaster_config"

    def test_get_or_create_uses_matmaster_config_dir(self, tmp_path):
        """Verify get_or_create() uses matmaster_config/ path."""
        from unittest.mock import patch

        from matmaster.core.playground import PlaygroundManager

        mgr = PlaygroundManager(tmp_path)
        cfg_dir = tmp_path / "matmaster_config"
        cfg_dir.mkdir()
        with patch("matmaster.core.playground.Playground") as mock_pg:
            mock_pg.return_value = mock_pg
            mgr.get_or_create("test-session")
            call_args = mock_pg.call_args
            config_path = call_args.kwargs.get("config_path") or call_args[0][0]
            assert "matmaster_config" in str(config_path)
