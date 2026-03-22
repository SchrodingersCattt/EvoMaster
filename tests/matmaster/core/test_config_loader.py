"""Tests for config_loader -- YAML config loading utility."""
from __future__ import annotations
from pathlib import Path
import pytest
from matmaster.core.config_loader import load_config


class TestLoadConfig:
    def test_load_from_dict(self) -> None:
        """load_config accepts a dict directly (pass-through)."""
        config = {"name": "direct", "termination": {"max_turns": 50}}
        result = load_config(config)
        assert result == config

    def test_load_from_yaml_file(self, tmp_path: Path) -> None:
        """load_config loads a YAML file when given a path."""
        yaml_content = "name: direct\ntermination:\n  max_turns: 100\n"
        config_file = tmp_path / "test.yaml"
        config_file.write_text(yaml_content)
        result = load_config(config_file)
        assert result["name"] == "direct"
        assert result["termination"]["max_turns"] == 100

    def test_load_from_string_path(self, tmp_path: Path) -> None:
        """load_config accepts a string path."""
        yaml_content = "name: planner\n"
        config_file = tmp_path / "test.yaml"
        config_file.write_text(yaml_content)
        result = load_config(str(config_file))
        assert result["name"] == "planner"

    def test_load_nonexistent_file_raises(self) -> None:
        """load_config raises FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/config.yaml")

    def test_load_expands_user_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config expands ~ in paths."""
        yaml_content = "name: test\n"
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)
        monkeypatch.setenv("HOME", str(tmp_path))
        result = load_config("~/config.yaml")
        assert result["name"] == "test"
