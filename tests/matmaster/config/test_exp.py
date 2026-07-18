"""Tests for matmaster.config.exp -- ExpConfig model."""

from __future__ import annotations

from matmaster.config.exp import (
    ExpConfig,
    ExpSkillsConfig,
    ExpToolsConfig,
    with_excluded_builtin_tools,
)


class TestExpToolsConfig:
    def test_defaults(self):
        cfg = ExpToolsConfig()
        assert cfg.builtin == ["*"]
        assert cfg.excluded_builtin == []
        assert cfg.mcp == "*"

    def test_add_excluded_builtin_tools(self):
        cfg = with_excluded_builtin_tools(
            ExpConfig(),
            ["Bohrium", "", "Bohrium"],
        )
        assert cfg.tools.builtin == ["*"]
        assert cfg.tools.excluded_builtin == ["Bohrium"]


class TestExpConfig:
    def test_defaults(self):
        cfg = ExpConfig()
        assert cfg.name == "direct"
        assert cfg.max_turns == 100
        assert cfg.developer_instructions == ""
        assert cfg.tools.builtin == ["*"]

    def test_subagent_metadata_defaults(self):
        cfg = ExpConfig()
        assert cfg.when_to_use == ""
        assert cfg.read_only is False
        assert cfg.visible_as_subagent is True
        assert cfg.context_mode == "fresh"
        assert cfg.result_style == "summary"

    def test_from_toml_dict(self):
        """Simulate what tomllib.load() would produce from direct.toml."""
        data = {
            "name": "direct",
            "max_turns": 200,
            "developer_instructions": "You are Mat Master.",
            "tools": {"builtin": ["*"], "mcp": "*"},
        }
        cfg = ExpConfig.model_validate(data)
        assert cfg.name == "direct"
        assert cfg.max_turns == 200
        assert cfg.developer_instructions == "You are Mat Master."

    def test_skills_and_compaction_accepted(self):
        data = {
            "name": "test",
            "skills": {"enabled": True},
            "compaction": {"context_limit": 64000},
        }
        cfg = ExpConfig.model_validate(data)
        assert cfg.name == "test"
        assert cfg.skills.enabled is True
        assert "enabled" not in type(cfg.compaction).model_fields
        assert cfg.compaction.context_limit == 64000

    def test_system_prompt_default(self):
        cfg = ExpConfig()
        assert cfg.system_prompt == ""

    def test_system_prompt_from_dict(self):
        data = {
            "name": "direct",
            "system_prompt": "You are Mat Master.",
        }
        cfg = ExpConfig.model_validate(data)
        assert cfg.system_prompt == "You are Mat Master."

    def test_developer_instructions_multiline(self):
        """Multiline strings from toml are preserved."""
        data = {
            "developer_instructions": "Line 1\nLine 2\nLine 3",
        }
        cfg = ExpConfig.model_validate(data)
        assert "Line 2" in cfg.developer_instructions

    def test_subagent_metadata_from_dict(self):
        cfg = ExpConfig.model_validate(
            {
                "name": "explore",
                "description": "Read-only explorer",
                "when_to_use": "Use for codebase discovery",
                "read_only": True,
                "visible_as_subagent": False,
                "context_mode": "fresh",
                "result_style": "findings",
            }
        )
        assert cfg.when_to_use == "Use for codebase discovery"
        assert cfg.read_only is True
        assert cfg.visible_as_subagent is False
        assert cfg.context_mode == "fresh"
        assert cfg.result_style == "findings"


class TestExpSkillsConfig:
    def test_defaults(self):
        cfg = ExpSkillsConfig()
        assert cfg.enabled is False
        assert cfg.skills_root == ""
        assert cfg.cache_dir == ""
        assert cfg.config_dir == ""
        assert cfg.mcp_config_file == ""
        assert cfg.mcp_runtime_file == "mcp.yaml"

    def test_from_dict(self):
        cfg = ExpSkillsConfig(
            enabled=True,
            skills_root="matmaster/skills",
            cache_dir="matmaster/cache",
            config_dir="config",
            mcp_config_file="mcp_config.json",
        )
        assert cfg.enabled is True
        assert cfg.skills_root == "matmaster/skills"


class TestExpConfigWithSkills:
    def test_exp_config_includes_skills(self):
        data = {
            "name": "direct",
            "skills": {
                "enabled": True,
                "skills_root": "matmaster/skills",
                "cache_dir": "matmaster/cache",
                "config_dir": "config",
                "mcp_config_file": "mcp_config.json",
            },
        }
        cfg = ExpConfig.model_validate(data)
        assert cfg.skills.enabled is True
        assert cfg.skills.cache_dir == "matmaster/cache"

    def test_exp_config_skills_defaults_when_absent(self):
        cfg = ExpConfig.model_validate({"name": "direct"})
        assert cfg.skills.enabled is False


def test_expconfig_llm_defaults_none():
    assert ExpConfig().llm is None


def test_expconfig_llm_accepts_profile_key():
    cfg = ExpConfig(llm="matmaster/gpt-5.5")
    assert cfg.llm == "matmaster/gpt-5.5"


def test_expconfig_rejects_unknown_field():
    # extra="forbid" 仍生效，llm 不削弱严格性
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExpConfig(llmm="typo")
