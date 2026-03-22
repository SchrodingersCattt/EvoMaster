"""Tests for SkillHitHook."""

from __future__ import annotations

from unittest.mock import MagicMock

from matmaster.engine.types import ToolCallData


class TestSkillHitHook:
    """SkillHitHook post_tool_call behavior."""

    def test_emits_skill_hit_event_for_skill_tool(self) -> None:
        """post_tool_call emits SkillHitEvent when tool_name starts with 'skill:'."""
        from matmaster.hooks.skill_hit import SkillHitHook
        from matmaster.types.events import SkillHitEvent

        bus = MagicMock()
        hook = SkillHitHook(bus=bus, source="MatMaster")
        tc = ToolCallData(id="tc-1", name="skill:vasp_runner", arguments={})
        hook.post_tool_call(tc, "result")

        bus.emit.assert_called_once()
        emitted = bus.emit.call_args[0][0]
        assert isinstance(emitted, SkillHitEvent)
        assert emitted.skill_name == "vasp_runner"
        assert emitted.source == "MatMaster"

    def test_does_nothing_for_non_skill_tool(self) -> None:
        """post_tool_call does nothing for non-skill tools."""
        from matmaster.hooks.skill_hit import SkillHitHook

        bus = MagicMock()
        hook = SkillHitHook(bus=bus)
        tc = ToolCallData(id="tc-1", name="bash", arguments={})
        hook.post_tool_call(tc, "result")

        bus.emit.assert_not_called()

    def test_does_nothing_for_skill_substring_in_middle(self) -> None:
        """post_tool_call does not trigger for tools with 'skill:' not at start."""
        from matmaster.hooks.skill_hit import SkillHitHook

        bus = MagicMock()
        hook = SkillHitHook(bus=bus)
        tc = ToolCallData(id="tc-1", name="run_skill:test", arguments={})
        hook.post_tool_call(tc, "result")

        bus.emit.assert_not_called()
