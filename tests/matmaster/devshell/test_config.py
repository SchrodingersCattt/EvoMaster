"""Tests for DevConfig model and loading."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


class TestDevConfig:
    def test_defaults(self) -> None:
        from matmaster.devshell.config import DevConfig

        cfg = DevConfig()
        assert cfg.llm.model == "gpt-4o"
        assert cfg.agent.max_turns == 20
        assert cfg.session.type == "local"
        assert cfg.tools.builtin == ["*"]

    def test_from_dict(self) -> None:
        from matmaster.devshell.config import DevConfig

        data = {
            "llm": {"model": "gpt-3.5-turbo", "api_key": "sk-test"},
            "agent": {"max_turns": 5},
        }
        cfg = DevConfig.model_validate(data)
        assert cfg.llm.model == "gpt-3.5-turbo"
        assert cfg.llm.api_key == "sk-test"
        assert cfg.agent.max_turns == 5

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
llm:
  model: gpt-4o-mini
  api_key: sk-yaml
agent:
  max_turns: 10
  identity: "Test bot"
"""
        config_file = tmp_path / "dev.yaml"
        config_file.write_text(yaml_content)
        cfg = load_dev_config(config_file)
        assert cfg.llm.model == "gpt-4o-mini"
        assert cfg.llm.api_key == "sk-yaml"
        assert cfg.agent.max_turns == 10
        assert cfg.agent.identity == "Test bot"

    def test_env_var_expansion(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from matmaster.devshell.config import load_dev_config

        monkeypatch.setenv("TEST_API_KEY", "sk-from-env")
        yaml_content = """
llm:
  api_key: ${TEST_API_KEY}
"""
        config_file = tmp_path / "dev.yaml"
        config_file.write_text(yaml_content)
        cfg = load_dev_config(config_file)
        assert cfg.llm.api_key == "sk-from-env"

    def test_file_not_found(self) -> None:
        from matmaster.devshell.config import load_dev_config

        with pytest.raises(FileNotFoundError):
            load_dev_config(Path("/nonexistent/dev.yaml"))

    def test_defaults_when_no_file(self) -> None:
        from matmaster.devshell.config import DevConfig

        cfg = DevConfig()
        assert cfg.llm.api_key == ""
        assert cfg.agent.name == "general"


class TestLLMConfigExtended:
    def test_timeout_defaults(self) -> None:
        from matmaster.devshell.config import DevConfig

        cfg = DevConfig()
        assert cfg.llm.timeout == 300.0
        assert cfg.llm.stream_timeout is None
        assert cfg.llm.stream_idle_timeout is None
        assert cfg.llm.max_retries == 3
        assert cfg.llm.retry_delay == 1.0

    def test_custom_timeout_from_dict(self) -> None:
        from matmaster.devshell.config import DevConfig

        cfg = DevConfig.model_validate({
            "llm": {
                "timeout": 60.0,
                "stream_timeout": 30.0,
                "stream_idle_timeout": 10.0,
                "max_retries": 5,
                "retry_delay": 2.0,
            }
        })
        assert cfg.llm.timeout == 60.0
        assert cfg.llm.stream_timeout == 30.0
        assert cfg.llm.stream_idle_timeout == 10.0
        assert cfg.llm.max_retries == 5
        assert cfg.llm.retry_delay == 2.0
