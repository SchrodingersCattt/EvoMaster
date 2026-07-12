"""Tests for matmaster.config.loader typed accessors."""

from __future__ import annotations

from pathlib import Path

import pytest

from matmaster.config.exp import DEFAULT_MODE, SUPPORTED_MODES, ExpConfig
from matmaster.config.llm import LLMConfig
from matmaster.config.loader import (
    list_available_exps,
    list_model_visible_exps,
    load_base_system_prompt,
    load_exp_config,
    load_llm_config,
)

# Minimal YAML content for tests
_YAML_CONTENT = """\
llm:
  providers:
    litellm:
      transport: "chat_completions"
      api_key: "sk-test"
      base_url: "http://litellm-proxy"
  profiles:
    opus:
      provider: "litellm"
      model: "claude-opus-4-6"
      context_limit: 200000
      temperature: 0.7
    sonnet:
      provider: "litellm"
      model: "claude-sonnet-4-6"
      context_limit: 128000
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
                "providers": {
                    "litellm": {
                        "transport": "chat_completions",
                        "api_key": "sk-test",
                    }
                },
                "profiles": {
                    "p1": {
                        "provider": "litellm",
                        "model": "m1",
                        "context_limit": 200_000,
                    }
                },
                "default": "p1",
            }
        }
        cfg = load_llm_config(raw)
        assert cfg.profiles["p1"].model == "m1"

    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_llm_config("/nonexistent/config.yaml")

    def test_env_var_expansion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TEST_API_KEY", "sk-secret")
        yaml = (
            'llm:\n'
            '  providers:\n'
            '    litellm:\n'
            '      transport: "chat_completions"\n'
            '      api_key: "${TEST_API_KEY}"\n'
            '  profiles:\n'
            '    p1:\n'
            '      provider: "litellm"\n'
            '      model: "m1"\n'
            '      context_limit: 200000\n'
            '  default: "p1"\n'
        )
        f = tmp_path / "config.yaml"
        f.write_text(yaml)
        cfg = load_llm_config(f)
        assert cfg.providers["litellm"].api_key == "sk-secret"


class TestLoadLlmConfigNormalized:
    """load_llm_config with normalized schema (providers + profiles)."""

    def test_load_normalized_yaml(self, tmp_path: Path) -> None:
        yaml_content = """
default: "p1"
providers:
  litellm:
    transport: "chat_completions"
    api_key: "test-key"
profiles:
  p1:
    provider: "litellm"
    model: "test-model"
    context_limit: 200000
"""
        f = tmp_path / "llm_config.yaml"
        f.write_text(yaml_content)
        cfg = load_llm_config(f)
        assert "p1" in cfg.profiles
        assert "litellm" in cfg.providers
        assert cfg.resolve(model_override="p1").profile.model == "test-model"

    def test_repo_llm_config_profiles_current_gpt56_sol(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]

        cfg = load_llm_config(repo_root / "config" / "llm_config.yaml")
        resolved = cfg.resolve(model_override="matmaster/gpt-5.6-sol")

        assert resolved.profile_key == "matmaster/gpt-5.6-sol"
        assert resolved.profile.model == "matmaster/gpt-5.6-sol"

    def test_repo_llm_config_includes_native_anthropic_opus(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]

        cfg = load_llm_config(repo_root / "config" / "llm_config.yaml")
        resolved = cfg.resolve(model_override="global.anthropic.claude-opus-4-6-v1")

        assert resolved.provider.transport == "anthropic_messages"
        # NOTE: Do not assert literal provider.api_key / base_url `${...}` placeholders:
        # load_llm_config expands ${VAR} unconditionally (env value when set,
        # empty string when missing), so placeholders are not preserved.
        assert resolved.provider.vendor == "bedrock"
        assert resolved.profile.model == "global.anthropic.claude-opus-4-6-v1"
        assert resolved.profile.reasoning_effort == "max"
        assert resolved.profile.supports_vision is True
        assert resolved.profile.max_tokens == 32_000
        assert resolved.profile.prompt_cache is not None
        assert resolved.profile.prompt_cache.system_prompt_breakpoint is True
        assert resolved.profile.prompt_cache.automatic is True
        assert resolved.profile.prompt_cache.latest_user_breakpoint is True
        assert resolved.profile.prompt_cache.tool_result_breakpoint is True
        assert resolved.profile.prompt_cache.flexible_breakpoint is True
        assert resolved.profile.prompt_cache.max_breakpoints == 4
        assert resolved.profile.prompt_cache.min_flexible_chars == 1000


class TestRealLlmConfigResponsesMigration:
    def test_litellm_responses_provider_and_gpt_profile_migrated(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        cfg = load_llm_config(repo_root / "config" / "llm_config.yaml")

        assert cfg.providers["litellm-responses"].transport == "responses"

        gpt = cfg.profiles["matmaster/gpt-5.6-sol"]
        assert gpt.provider == "litellm-responses"
        assert gpt.model == "matmaster/gpt-5.6-sol"
        assert gpt.reasoning_effort == "xhigh"
        assert gpt.reasoning_summary == "detailed"

        resolved = cfg.resolve(model_override="matmaster/gpt-5.6-sol")
        assert resolved.provider.transport == "responses"

        assert cfg.default == "matmaster/qwen3.7-max"
        assert cfg.profiles["matmaster/qwen3.7-max"].provider == "litellm-qwen"
        glm = cfg.profiles["matmaster/zhipu/glm-5.2"]
        assert glm.provider == "litellm"
        assert glm.model == "matmaster/zhipu/glm-5.2"
        assert glm.context_limit == 1_000_000
        assert glm.supports_vision is False

    def test_migrated_config_builds_responses_transport(self) -> None:
        from matmaster.providers.llm_factory import build_provider
        from matmaster.providers.transports.responses import ResponsesTransport

        repo_root = Path(__file__).resolve().parents[3]
        cfg = load_llm_config(repo_root / "config" / "llm_config.yaml")

        provider = build_provider(cfg, model_override="matmaster/gpt-5.6-sol")
        assert isinstance(provider, ResponsesTransport)
        assert provider._model == "matmaster/gpt-5.6-sol"


class TestRealLlmConfigVendorWiring:
    def test_vendor_providers_and_profile_pointing(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        cfg = load_llm_config(repo_root / "config" / "llm_config.yaml")

        assert cfg.providers["litellm-qwen"].transport == "chat_completions"
        assert cfg.providers["litellm-qwen"].vendor == "qwen"
        assert cfg.providers["litellm-deepseek"].transport == "chat_completions"
        assert cfg.providers["litellm-deepseek"].vendor == "deepseek"
        assert cfg.providers["litellm"].vendor is None
        assert cfg.providers["litellm-anthropic"].vendor == "bedrock"

        assert cfg.profiles["matmaster/qwen3.7-max"].provider == "litellm-qwen"
        assert cfg.profiles["matmaster/DeepSeek-v4-Pro"].provider == "litellm-deepseek"
        assert cfg.profiles["gemini-3.1-pro-preview"].provider == "litellm"

    def test_default_profile_builds_qwen_vendor_transport(self) -> None:
        from matmaster.providers.llm_factory import build_provider
        from matmaster.providers.transports.chat_completions import (
            QwenChatCompletionsTransport,
        )

        repo_root = Path(__file__).resolve().parents[3]
        cfg = load_llm_config(repo_root / "config" / "llm_config.yaml")

        provider = build_provider(cfg)
        assert isinstance(provider, QwenChatCompletionsTransport)


class TestLoadExpConfig:
    """Tests for load_exp_config() -- toml-based loading."""

    def test_load_direct(self, tmp_path):
        """Load a valid toml file by name."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "direct.toml").write_text(
            'name = "direct"\nmax_turns = 200\n'
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
            'name = "test"\n' "developer_instructions = 'Use ${FOO} as template var'\n"
        )
        cfg = load_exp_config("test", exps_dir=exps_dir)
        assert "${FOO}" in cfg.developer_instructions

    def test_default_exps_dir(self):
        """Default exps_dir resolves to matmaster/exps/ and can load direct.toml."""
        cfg = load_exp_config("direct")
        assert cfg.name == "direct"


class TestExpDiscovery:
    def test_list_model_visible_exps_returns_structured_metadata(self, tmp_path):
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "direct.toml").write_text(
            'name = "direct"\n'
            'description = "exec agent"\n'
            'when_to_use = "Use for execution"\n'
            'read_only = false\n'
            'visible_as_subagent = true\n'
            "[tools]\n"
            'builtin = ["Bash", "Read"]\n'
            'mcp = "*"\n'
        )

        visible = list_model_visible_exps(exps_dir=exps_dir)

        assert len(visible) == 1
        assert visible[0].name == "direct"
        assert visible[0].description == "exec agent"
        assert visible[0].when_to_use == "Use for execution"
        assert visible[0].read_only is False
        assert "Bash" in visible[0].tools_summary
        assert "MCP: all" in visible[0].tools_summary

    def test_list_model_visible_exps_skips_hidden_exp(self, tmp_path):
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "hidden.toml").write_text(
            'name = "hidden"\n'
            'description = "hidden agent"\n'
            'visible_as_subagent = false\n'
        )

        assert list_model_visible_exps(exps_dir=exps_dir) == []

    def test_list_available_exps_keeps_legacy_all_exp_listing(self, tmp_path):
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "hidden.toml").write_text(
            'name = "hidden"\n'
            'description = "hidden agent"\n'
            'visible_as_subagent = false\n'
        )

        assert list_available_exps(exps_dir=exps_dir) == [("hidden", "hidden agent")]


class TestBaseTomlMerge:
    """Tests for _base.toml system_prompt merge semantics."""

    def test_base_present_exp_no_override(self, tmp_path):
        """_base.toml system_prompt used when exp toml has none."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "_base.toml").write_text("system_prompt = 'Base system prompt'\n")
        (exps_dir / "test.toml").write_text(
            'name = "test"\ndeveloper_instructions = "DI"\n'
        )
        cfg = load_exp_config("test", exps_dir=exps_dir)
        assert cfg.system_prompt == "Base system prompt"

    def test_exp_overrides_base(self, tmp_path):
        """Exp toml system_prompt overrides _base.toml."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "_base.toml").write_text("system_prompt = 'Base prompt'\n")
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
        (exps_dir / "_base.toml").write_text("system_prompt = 'Use ${FOO} literally'\n")
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
        (exps_dir / "_base.toml").write_text("system_prompt = 'Hello from base'\n")
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


class TestModeConstants:
    """UI mode whitelist constants exposed by matmaster.config.exp."""

    def test_supported_modes_exact_set(self) -> None:
        assert SUPPORTED_MODES == frozenset({"direct", "planner"})

    def test_default_mode_is_direct(self) -> None:
        assert DEFAULT_MODE == "direct"

    def test_default_mode_is_in_supported(self) -> None:
        assert DEFAULT_MODE in SUPPORTED_MODES


class TestPlannerExpConfig:
    """planner.toml 的加载与 subagent 可见性——依赖真实文件 matmaster/exps/planner.toml。"""

    def test_load_exp_config_planner(self) -> None:
        cfg = load_exp_config("planner")
        assert isinstance(cfg, ExpConfig)
        assert cfg.name == "planner"
        assert cfg.read_only is False
        assert cfg.skills.enabled is True
        assert cfg.visible_as_subagent is False, (
            "planner 不应作为 AgentTool 的 subagent enum 暴露；与 "
            "when_to_use='NEVER' 的意图保持一致"
        )

    def test_planner_not_in_model_visible_exps(self) -> None:
        """list_model_visible_exps() 不应返回 planner（被 visible_as_subagent=false 过滤）。"""
        metas = list_model_visible_exps()
        names = {m.name for m in metas}
        assert (
            "planner" not in names
        ), f"planner 意外出现在 model-visible exp 列表中；当前 names={names}"
        # direct 仍应可见——保留正回归断言
        assert "direct" in names
