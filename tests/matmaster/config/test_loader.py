"""Tests for matmaster.config.loader typed accessors."""
from __future__ import annotations

from pathlib import Path

import pytest

from matmaster.config.exp import ExpConfig
from matmaster.config.llm import LLMConfig
from matmaster.config.loader import load_base_system_prompt, load_exp_config, load_llm_config

# Minimal YAML content for tests
_YAML_CONTENT = """\
llm:
  opus:
    provider: "openai"
    model: "claude-opus-4-6"
    temperature: 0.7
  sonnet:
    provider: "openai"
    model: "claude-sonnet-4-6"
    temperature: 0.5
  default: "opus"

agents:
  general:
    llm: "opus"
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
        assert cfg.default == "opus"
        assert cfg.profiles["opus"].model == "claude-opus-4-6"

    def test_from_string_path(self, yaml_file: Path) -> None:
        cfg = load_llm_config(str(yaml_file))
        assert "sonnet" in cfg.profiles

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

    def test_default_exps_dir(self):
        """Default exps_dir resolves to matmaster/exps/ and can load direct.toml."""
        cfg = load_exp_config("direct")
        assert cfg.name == "direct"


class TestBaseTomlMerge:
    """Tests for _base.toml system_prompt merge semantics."""

    def test_base_present_exp_no_override(self, tmp_path):
        """_base.toml system_prompt used when exp toml has none."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "_base.toml").write_text(
            "system_prompt = 'Base system prompt'\n"
        )
        (exps_dir / "test.toml").write_text(
            'name = "test"\ndeveloper_instructions = "DI"\n'
        )
        cfg = load_exp_config("test", exps_dir=exps_dir)
        assert cfg.system_prompt == "Base system prompt"

    def test_exp_overrides_base(self, tmp_path):
        """Exp toml system_prompt overrides _base.toml."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "_base.toml").write_text(
            "system_prompt = 'Base prompt'\n"
        )
        (exps_dir / "test.toml").write_text(
            'name = "test"\nsystem_prompt = "Exp override"\n'
        )
        cfg = load_exp_config("test", exps_dir=exps_dir)
        assert cfg.system_prompt == "Exp override"

    def test_base_missing(self, tmp_path, caplog):
        """Missing _base.toml yields empty system_prompt with warning."""
        import logging

        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "test.toml").write_text('name = "test"\n')
        with caplog.at_level(logging.WARNING):
            cfg = load_exp_config("test", exps_dir=exps_dir)
        assert cfg.system_prompt == ""
        assert "_base.toml" in caplog.text

    def test_base_extra_fields_ignored(self, tmp_path):
        """Non-system_prompt fields in _base.toml do not pollute ExpConfig."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "_base.toml").write_text(
            'system_prompt = "Base"\nname = "SHOULD_NOT_LEAK"\n'
        )
        (exps_dir / "test.toml").write_text('name = "test"\n')
        cfg = load_exp_config("test", exps_dir=exps_dir)
        assert cfg.name == "test"
        assert cfg.system_prompt == "Base"

    def test_base_system_prompt_not_env_expanded(self, tmp_path, monkeypatch):
        """${...} in _base.toml system_prompt preserved verbatim."""
        monkeypatch.setenv("FOO", "bar")
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "_base.toml").write_text(
            "system_prompt = 'Use ${FOO} literally'\n"
        )
        (exps_dir / "test.toml").write_text('name = "test"\n')
        cfg = load_exp_config("test", exps_dir=exps_dir)
        assert "${FOO}" in cfg.system_prompt

    def test_exp_discovery_excludes_underscore_prefix(self, tmp_path):
        """Error message for unknown exp does not list _base."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "_base.toml").write_text("system_prompt = 'x'\n")
        (exps_dir / "direct.toml").write_text('name = "direct"\n')
        with pytest.raises(FileNotFoundError) as exc_info:
            load_exp_config("nope", exps_dir=exps_dir)
        assert "_base" not in str(exc_info.value)
        assert "direct" in str(exc_info.value)

    def test_underscore_prefix_name_rejected(self, tmp_path):
        """load_exp_config('_base') raises ValueError, not silently loads."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "_base.toml").write_text("system_prompt = 'x'\n")
        with pytest.raises(ValueError, match="reserved"):
            load_exp_config("_base", exps_dir=exps_dir)


class TestLoadBaseSystemPrompt:
    """Tests for the standalone load_base_system_prompt() helper."""

    def test_returns_system_prompt(self, tmp_path):
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "_base.toml").write_text(
            "system_prompt = 'Hello from base'\n"
        )
        result = load_base_system_prompt(exps_dir=exps_dir)
        assert result == "Hello from base"

    def test_missing_base_returns_empty(self, tmp_path, caplog):
        import logging

        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        with caplog.at_level(logging.WARNING):
            result = load_base_system_prompt(exps_dir=exps_dir)
        assert result == ""
        assert "_base.toml" in caplog.text
