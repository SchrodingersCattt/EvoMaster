"""Focused tests for BohriumTool description/prompt balancing."""

from __future__ import annotations

from matmaster.tools.builtin.bohrium_tool import BohriumTool


class TestBohriumToolDescriptionPromptRebalance:
    def test_description_is_capability_summary(self) -> None:
        assert "HPC platform operations" in BohriumTool.description
        assert 'action="submit"' not in BohriumTool.description
        assert 'action="poll"' not in BohriumTool.description
        assert 'action="download"' not in BohriumTool.description
        assert len(BohriumTool.description) < 250

    def test_prompt_retains_usage_section_and_absorbs_action_details(
        self,
    ) -> None:
        tool = BohriumTool()
        prompt = tool.prompt()
        assert prompt is not None
        assert "Bohrium tool usage" in prompt
        assert "submit" in prompt
        assert "poll" in prompt
        assert "download" in prompt
        assert "kill" in prompt
        assert "list_images" in prompt
        assert "list_machines" in prompt
