"""Tests for matmaster.config.exp -- ExpConfig model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from matmaster.config.exp import ExpConfig, ExpToolsConfig


class TestExpToolsConfig:
    def test_defaults(self):
        cfg = ExpToolsConfig()
        assert cfg.builtin == ["*"]
        assert cfg.mcp == "*"


class TestExpConfig:
    def test_defaults(self):
        cfg = ExpConfig()
        assert cfg.name == "direct"
        assert cfg.mode == "direct"
        assert cfg.max_turns == 100
        assert cfg.guards == []
        assert cfg.developer_instructions == ""
        assert cfg.tools.builtin == ["*"]

    def test_from_toml_dict(self):
        """Simulate what tomllib.load() would produce from direct.toml."""
        data = {
            "name": "direct",
            "mode": "direct",
            "max_turns": 200,
            "guards": [],
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

    def test_skills_mcp_compaction_not_accepted(self):
        """These fields were removed -- they should be silently ignored via extra=ignore."""
        data = {
            "name": "test",
            "skills": {"enabled": True},
            "mcp": {"servers": []},
            "compaction": {"enabled": True},
        }
        cfg = ExpConfig.model_validate(data)
        assert cfg.name == "test"
        assert not hasattr(cfg, "skills")
        assert not hasattr(cfg, "compaction")

    def test_developer_instructions_multiline(self):
        """Multiline strings from toml are preserved."""
        data = {
            "developer_instructions": "Line 1\nLine 2\nLine 3",
        }
        cfg = ExpConfig.model_validate(data)
        assert "Line 2" in cfg.developer_instructions
