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


class TestLoadLlmConfigNormalized:
    """load_llm_config with normalized schema (profiles + routes)."""

    def test_load_normalized_yaml(self, tmp_path: Path) -> None:
        yaml_content = """
default: "p1"
profiles:
  p1:
    model: "test-model"
    api_key: "test-key"
routes:
  test-route:
    profile: "p1"
    model: "test-model"
"""
        f = tmp_path / "llm_config.yaml"
        f.write_text(yaml_content)
        cfg = load_llm_config(f)
        assert "p1" in cfg.profiles
        assert "test-route" in cfg.routes
        assert cfg.routes["test-route"].profile == "p1"


class TestLoadExpConfig:
    """Tests for load_exp_config() -- toml-based loading."""

    def test_load_direct(self, tmp_path):
        """Load a valid toml file by name."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "direct.toml").write_text(
            'name = "direct"\nmode = "direct"\nmax_turns = 200\n'
            'developer_instructions = "You are Mat Master."\n'
            "\n[tools]\nbuiltin = ['*']\nmcp = '*'\n",
            encoding="utf-8",
        )
        cfg = load_exp_config("direct", exps_dir=exps_dir)
        assert isinstance(cfg, ExpConfig)
        assert cfg.name == "direct"
        assert cfg.max_turns == 200
        assert cfg.developer_instructions == "You are Mat Master."

    def test_unknown_name_raises(self, tmp_path):
        """Unknown exp name raises FileNotFoundError with available list."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "direct.toml").write_text('name = "direct"\n')
        with pytest.raises(FileNotFoundError, match="unknown_exp"):
            load_exp_config("unknown_exp", exps_dir=exps_dir)

    def test_error_message_lists_available(self, tmp_path):
        """FileNotFoundError message includes available exp names."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "direct.toml").write_text('name = "direct"\n')
        (exps_dir / "planner.toml").write_text('name = "planner"\n')
        with pytest.raises(FileNotFoundError, match="direct") as exc_info:
            load_exp_config("nope", exps_dir=exps_dir)
        assert "planner" in str(exc_info.value)

    def test_env_var_expansion(self, tmp_path, monkeypatch):
        """${ENV} patterns are expanded in non-developer_instructions fields."""
        monkeypatch.setenv("TEST_MCP", "custom")
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "test.toml").write_text(
            'name = "test"\n\n[tools]\nbuiltin = ["*"]\nmcp = "${TEST_MCP}"\n'
        )
        cfg = load_exp_config("test", exps_dir=exps_dir)
        assert cfg.tools.mcp == "custom"

    def test_developer_instructions_not_expanded(self, tmp_path, monkeypatch):
        """${...} in developer_instructions is preserved, not expanded."""
        monkeypatch.setenv("FOO", "bar")
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "test.toml").write_text(
            'name = "test"\n'
            "developer_instructions = 'Use ${FOO} as template var'\n"
        )
        cfg = load_exp_config("test", exps_dir=exps_dir)
        assert "${FOO}" in cfg.developer_instructions

    def test_mode_contract_not_expanded(self, tmp_path, monkeypatch):
        """${...} in mode_contract is preserved, not expanded."""
        monkeypatch.setenv("FOO", "bar")
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "test.toml").write_text(
            'name = "test"\n'
            "mode_contract = 'Use ${FOO} as template var'\n"
        )
        cfg = load_exp_config("test", exps_dir=exps_dir)
        assert "${FOO}" in cfg.mode_contract

    def test_mode_contract_loaded(self, tmp_path):
        """mode_contract field is loaded from toml."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "direct.toml").write_text(
            'name = "direct"\n'
            'mode_contract = "Execute directly."\n'
            'developer_instructions = "You are Mat Master."\n'
            "\n[tools]\nbuiltin = ['*']\nmcp = '*'\n",
            encoding="utf-8",
        )
        cfg = load_exp_config("direct", exps_dir=exps_dir)
        assert cfg.mode_contract == "Execute directly."

    def test_mode_contract_reaches_system_prompt(self, tmp_path):
        """End-to-end: toml mode_contract appears in built system prompt."""
        from matmaster.core.exp import Exp
        from matmaster.types.context import PlaygroundContext
        from tests.matmaster.core.conftest import MockLLMProvider

        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "test.toml").write_text(
            'name = "test"\n'
            'developer_instructions = "I am Test Agent."\n'
            'mode_contract = "Execute tasks in test mode."\n'
            "\n[tools]\nbuiltin = []\nmcp = ''\n",
            encoding="utf-8",
        )
        cfg = load_exp_config("test", exps_dir=exps_dir)
        exp = Exp(cfg)

        ctx = PlaygroundContext(
            workdir=tmp_path,
            session_type="local",
            cache_area=tmp_path / "cache",
            llm_provider=MockLLMProvider(),
        )
        runtime = exp.build_runtime(ctx)

        assert "I am Test Agent." in runtime.spec.system_prompt
        assert "Execute tasks in test mode." in runtime.spec.system_prompt

    def test_default_exps_dir(self):
        """Default exps_dir resolves to matmaster/exps/ and can load direct.toml."""
        cfg = load_exp_config("direct")
        assert cfg.name == "direct"
