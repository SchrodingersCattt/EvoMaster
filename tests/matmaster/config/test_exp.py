"""Tests for ExpConfig -- typed Exp assembly configuration."""
from __future__ import annotations

from matmaster.config.exp import ExpConfig, ExpToolsConfig


class TestExpToolsConfig:
    def test_defaults(self) -> None:
        t = ExpToolsConfig()
        assert t.builtin == ["*"]
        assert t.mcp == "*"


class TestExpConfig:
    def test_defaults_match_exp_assemble(self) -> None:
        """Default values match what Exp.assemble() previously hardcoded."""
        cfg = ExpConfig()
        assert cfg.name == "direct"
        assert cfg.mode == "direct"
        assert cfg.max_turns == 100
        assert cfg.guards == []
        assert cfg.tools.builtin == ["*"]
        assert cfg.skills == {}
        assert cfg.mcp == {}

    def test_from_agents_general_dict(self) -> None:
        """Load from a dict shaped like YAML agents.general section.

        extra='ignore' discards fields not in ExpConfig (context, compaction, etc.).
        """
        raw = {
            "llm": "litellm",
            "max_turns": 200,
            "tools": {"builtin": ["*"], "mcp": "*"},
            "context": {"max_tokens": 180000},
            "system_prompt_file": "prompts/system.txt",
        }
        cfg = ExpConfig.model_validate(raw)
        assert cfg.max_turns == 200
        assert cfg.tools.mcp == "*"
        # extra fields silently ignored
        assert not hasattr(cfg, "context")
        assert not hasattr(cfg, "system_prompt_file")

    def test_runtime_override(self) -> None:
        """Runtime dict merges on top of YAML-loaded values."""
        base = {"max_turns": 200, "tools": {"builtin": ["*"]}}
        runtime = {"skills": {"enabled": True}, "mcp": {"servers": ["s1"]}}
        merged = {**base, **runtime}
        cfg = ExpConfig.model_validate(merged)
        assert cfg.max_turns == 200
        assert cfg.skills == {"enabled": True}
        assert cfg.mcp == {"servers": ["s1"]}

    def test_model_dump_for_exp(self) -> None:
        """model_dump() produces a dict consumable by Exp(config=...)."""
        cfg = ExpConfig(name="planner", mode="planner", max_turns=50)
        d = cfg.model_dump()
        assert d["name"] == "planner"
        assert d["mode"] == "planner"
        assert d["max_turns"] == 50
