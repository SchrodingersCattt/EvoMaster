"""Tests for matmaster.types.tool_decision -- ToolDecision."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from matmaster.types.tool_decision import ToolDecision


class TestToolDecision:
    def test_tool_decision_allow(self) -> None:
        """ToolDecision with decision='allow' constructs correctly."""
        td = ToolDecision(decision="allow")
        assert td.decision == "allow"

    def test_tool_decision_deny_with_guidance(self) -> None:
        """ToolDecision with decision='deny' and reason/guidance."""
        td = ToolDecision(
            decision="deny",
            reason="file not read first",
            guidance="Please read the file before modifying it.",
        )
        assert td.decision == "deny"
        assert td.reason == "file not read first"
        assert td.guidance == "Please read the file before modifying it."

    def test_tool_decision_frozen(self) -> None:
        """ToolDecision is frozen -- assignment raises ValidationError."""
        td = ToolDecision(decision="allow")
        with pytest.raises(ValidationError):
            td.decision = "deny"

    def test_tool_decision_defaults(self) -> None:
        """ToolDecision has correct defaults."""
        td = ToolDecision(decision="allow")
        assert td.reason == ""
        assert td.guidance is None

    def test_tool_decision_invalid_decision_rejected(self) -> None:
        """ToolDecision rejects invalid decision values."""
        with pytest.raises(ValidationError):
            ToolDecision(decision="maybe")
