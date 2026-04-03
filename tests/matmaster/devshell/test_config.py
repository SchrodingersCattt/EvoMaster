"""Tests for DevConfig model."""

from __future__ import annotations


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
