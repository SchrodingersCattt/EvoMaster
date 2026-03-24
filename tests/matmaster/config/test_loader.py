"""Tests for matmaster.config.loader typed accessors."""
from __future__ import annotations

from pathlib import Path

import pytest

from matmaster.config.exp import ExpConfig
from matmaster.config.llm import LLMConfig
from matmaster.config.loader import load_exp_config, load_llm_config

# Minimal YAML content for tests
_YAML_CONTENT = """\
llm:
  litellm:
    provider: "openai"
    model: "claude-opus-4-6"
    temperature: 0.7
  azure:
    provider: "openai"
    model: "azure/gpt-5"
    temperature: 0.5
  default: "litellm"

agents:
  general:
    llm: "litellm"
    max_turns: 200
    tools:
      builtin: ["*"]
      mcp: "*"
    context:
      max_tokens: 180000
"""


@pytest.fixture()
def yaml_file(tmp_path: Path) -> Path:
    f = tmp_path / "config.yaml"
    f.write_text(_YAML_CONTENT)
    return f


class TestLoadLlmConfig:
    def test_from_yaml_path(self, yaml_file: Path) -> None:
        cfg = load_llm_config(yaml_file)
        assert isinstance(cfg, LLMConfig)
        assert cfg.default == "litellm"
        assert cfg.profiles["litellm"].model == "claude-opus-4-6"

    def test_from_string_path(self, yaml_file: Path) -> None:
        cfg = load_llm_config(str(yaml_file))
        assert "azure" in cfg.profiles

    def test_from_dict(self) -> None:
        raw = {
            "llm": {
                "p1": {"model": "m1"},
                "default": "p1",
            }
        }
        cfg = load_llm_config(raw)
        assert cfg.profiles["p1"].model == "m1"

    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_llm_config("/nonexistent/config.yaml")

    def test_env_var_expansion(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_API_KEY", "sk-secret")
        yaml = 'llm:\n  p1:\n    model: "m1"\n    api_key: "${TEST_API_KEY}"\n  default: "p1"\n'
        f = tmp_path / "config.yaml"
        f.write_text(yaml)
        cfg = load_llm_config(f)
        assert cfg.profiles["p1"].api_key == "sk-secret"


class TestLoadExpConfig:
    def test_from_yaml_path(self, yaml_file: Path) -> None:
        cfg = load_exp_config(yaml_file)
        assert isinstance(cfg, ExpConfig)
        assert cfg.max_turns == 200  # from YAML, not default 100

    def test_from_dict(self) -> None:
        raw = {
            "agents": {
                "general": {"max_turns": 150, "tools": {"builtin": ["bash"]}}
            }
        }
        cfg = load_exp_config(raw)
        assert cfg.max_turns == 150
        assert cfg.tools.builtin == ["bash"]

    def test_runtime_override(self, yaml_file: Path) -> None:
        cfg = load_exp_config(yaml_file, runtime={"skills": {"enabled": True}})
        assert cfg.skills == {"enabled": True}
        assert cfg.max_turns == 200  # YAML value preserved

    def test_custom_agent_name(self, tmp_path: Path) -> None:
        yaml = 'agents:\n  solver:\n    max_turns: 50\n    tools:\n      builtin: ["bash"]\n'
        f = tmp_path / "config.yaml"
        f.write_text(yaml)
        cfg = load_exp_config(f, agent_name="solver")
        assert cfg.max_turns == 50

    def test_missing_agent_uses_defaults(self) -> None:
        raw = {"agents": {}}
        cfg = load_exp_config(raw, agent_name="missing")
        assert cfg.max_turns == 100  # ExpConfig default
