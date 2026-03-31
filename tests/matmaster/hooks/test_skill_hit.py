"""Tests for SkillHitHook."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from matmaster.tools.tool_result import ToolResult
from matmaster.types.messages import ToolCallData


class TestSkillHitHook:
    """SkillHitHook post_tool_call behavior."""

    async def test_emits_skill_hit_event_for_use_skill(self) -> None:
        """post_tool_call emits SkillHitEvent when tool is use_skill with valid skill_name."""
        from matmaster.hooks.skill_hit import SkillHitHook
        from matmaster.types.events import SkillHitEvent

        bus = MagicMock(emit=AsyncMock())
        hook = SkillHitHook(bus=bus, source="MatMaster")
        tc = ToolCallData(
            id="tc-1",
            name="use_skill",
            arguments={"skill_name": "bohrium-job", "action": "get_info"},
        )
        await hook.post_tool_call(tc, ToolResult(content="result"))

        bus.emit.assert_called_once()
        emitted = bus.emit.call_args[0][0]
        assert isinstance(emitted, SkillHitEvent)
        assert emitted.skill_name == "bohrium-job"
        assert emitted.source == "MatMaster"

    async def test_does_nothing_for_non_skill_tool(self) -> None:
        """post_tool_call does nothing for non use_skill tools."""
        from matmaster.hooks.skill_hit import SkillHitHook

        bus = MagicMock(emit=AsyncMock())
        hook = SkillHitHook(bus=bus)
        tc = ToolCallData(id="tc-1", name="bash", arguments={})
        await hook.post_tool_call(tc, ToolResult(content="result"))

        bus.emit.assert_not_called()

    async def test_does_nothing_for_use_skill_without_skill_name(self) -> None:
        """post_tool_call does nothing when use_skill arguments lack skill_name."""
        from matmaster.hooks.skill_hit import SkillHitHook

        bus = MagicMock(emit=AsyncMock())
        hook = SkillHitHook(bus=bus)
        tc = ToolCallData(id="tc-1", name="use_skill", arguments={"action": "get_info"})
        await hook.post_tool_call(tc, ToolResult(content="result"))

        bus.emit.assert_not_called()

    async def test_does_nothing_for_non_string_skill_name(self) -> None:
        """post_tool_call does nothing when skill_name is not a string."""
        from matmaster.hooks.skill_hit import SkillHitHook

        bus = MagicMock(emit=AsyncMock())
        hook = SkillHitHook(bus=bus)
        tc = ToolCallData(id="tc-1", name="use_skill", arguments={"skill_name": 123})
        await hook.post_tool_call(tc, ToolResult(content="result"))

        bus.emit.assert_not_called()
