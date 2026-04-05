"""Tests for matmaster.config.exp -- ExpConfig model."""

from __future__ import annotations

from matmaster.config.exp import ExpConfig, ExpSkillsConfig, ExpToolsConfig


class TestExpToolsConfig:
    def test_defaults(self):
        cfg = ExpToolsConfig()
        assert cfg.builtin == ["*"]
        assert cfg.mcp == "*"


class TestExpConfig:
    def test_defaults(self):
        cfg = ExpConfig()
        assert cfg.name == "direct"
        assert cfg.max_turns == 100
        assert cfg.developer_instructions == ""
        assert cfg.tools.builtin == ["*"]

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

    def test_extra_fields_ignored(self):
        """Unknown fields from toml are silently ignored."""
        data = {"name": "test", "unknown_field": "value", "another": 123}
        cfg = ExpConfig.model_validate(data)
        assert cfg.name == "test"
        assert not hasattr(cfg, "unknown_field")

    def test_skills_and_compaction_accepted_mcp_ignored(self):
        """skills and compaction are real fields; mcp is silently ignored."""
        data = {
            "name": "test",
            "skills": {"enabled": True},
            "mcp": {"servers": []},
            "compaction": {"enabled": True, "context_window_tokens": 64000},
        }
        cfg = ExpConfig.model_validate(data)
        assert cfg.name == "test"
        assert cfg.skills.enabled is True
        assert cfg.compaction.enabled is True
        assert cfg.compaction.context_window_tokens == 64000
        # mcp is still ignored
        assert not hasattr(cfg, "mcp") or not isinstance(
            getattr(cfg, "mcp", None), dict
        )

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

    def test_mode_contract_rejected(self):
        """mode_contract field is ignored (extra='ignore')."""
        data = {"name": "direct", "mode_contract": "Execute directly."}
        cfg = ExpConfig.model_validate(data)
        assert not hasattr(cfg, "mode_contract")

    def test_developer_instructions_multiline(self):
        """Multiline strings from toml are preserved."""
        data = {
            "developer_instructions": "Line 1\nLine 2\nLine 3",
        }
        cfg = ExpConfig.model_validate(data)
        assert "Line 2" in cfg.developer_instructions


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
            skills_root="playground/mat_master/skills",
            cache_dir="matmaster/cache",
            config_dir="config",
            mcp_config_file="mcp_config.json",
        )
        assert cfg.enabled is True
        assert cfg.skills_root == "playground/mat_master/skills"


class TestExpConfigWithSkills:
    def test_exp_config_includes_skills(self):
        data = {
            "name": "direct",
            "skills": {
                "enabled": True,
                "skills_root": "playground/mat_master/skills",
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
