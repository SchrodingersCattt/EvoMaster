"""Tests for DevConfig model and loading."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestDevConfig:
    def test_defaults(self) -> None:
        from matmaster.devshell.config import DevConfig

        cfg = DevConfig()
        assert cfg.agent.max_turns == 20
        assert cfg.session.type == "local"
        assert cfg.tools.builtin == ["*"]

    def test_from_dict(self) -> None:
        from matmaster.devshell.config import DevConfig

        data = {
            "agent": {"max_turns": 5},
        }
        cfg = DevConfig.model_validate(data)
        assert cfg.agent.max_turns == 5

    def test_extra_keys_ignored(self) -> None:
        from matmaster.devshell.config import DevConfig

        cfg = DevConfig.model_validate(
            {"llm": {"model": "x"}, "agent": {"max_turns": 3}}
        )
        assert cfg.agent.max_turns == 3

    def test_identity_optional(self) -> None:
        from matmaster.devshell.config import DevConfig

        cfg = DevConfig()
        assert cfg.agent.identity is None

        cfg2 = DevConfig.model_validate({"agent": {"identity": "I am a scientist."}})
        assert cfg2.agent.identity == "I am a scientist."


class TestLoadDevConfig:
    def test_load_from_yaml(self, tmp_path: Path) -> None:
        from matmaster.devshell.config import load_dev_config

        yaml_content = """
agent:
  max_turns: 10
  identity: "Test bot"
"""
        config_file = tmp_path / "dev.yaml"
        config_file.write_text(yaml_content)
        cfg = load_dev_config(config_file)
        assert cfg.agent.max_turns == 10
        assert cfg.agent.identity == "Test bot"

    def test_env_var_expansion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from matmaster.devshell.config import load_dev_config

        monkeypatch.setenv("TEST_IDENTITY", "from-env")
        yaml_content = """
agent:
  identity: ${TEST_IDENTITY}
"""
        config_file = tmp_path / "dev.yaml"
        config_file.write_text(yaml_content)
        cfg = load_dev_config(config_file)
        assert cfg.agent.identity == "from-env"

    def test_file_not_found(self) -> None:
        from matmaster.devshell.config import load_dev_config

        with pytest.raises(FileNotFoundError):
            load_dev_config(Path("/nonexistent/dev.yaml"))

    def test_defaults_when_no_file(self) -> None:
        from matmaster.devshell.config import DevConfig

        cfg = DevConfig()
        assert cfg.agent.name == "general"
