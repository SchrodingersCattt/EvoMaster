"""Test that _should_emit_event_to_sse filters skill_hit on replay."""

from __future__ import annotations


class TestReplayFilterSkillHit:
    """History replay must not emit skill_hit to SSE."""

    def test_should_not_emit_skill_hit(self) -> None:
        """_should_emit_event_to_sse returns False for skill_hit events."""
        from src.services.stream_service import _should_emit_event_to_sse

        event = {"type": "skill_hit", "source": "MatMaster", "content": {"skill_name": "bohrium-job"}}
        assert _should_emit_event_to_sse(event) is False

    def test_still_emits_tool_call(self) -> None:
        """Sanity: tool_call events are still emitted."""
        from src.services.stream_service import _should_emit_event_to_sse

        event = {"type": "tool_call", "source": "MatMaster"}
        assert _should_emit_event_to_sse(event) is True
