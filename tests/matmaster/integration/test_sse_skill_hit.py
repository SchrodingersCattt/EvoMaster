"""Test that SSEHandler skips skill_hit events."""

from __future__ import annotations

from matmaster.types.events import SkillHitEvent


class TestSSEHandlerSkillHit:
    """SSEHandler must not push skill_hit to frontend."""

    def test_should_skip_skill_hit(self) -> None:
        """_should_skip returns True for SkillHitEvent."""
        from matmaster.integration.sse_handler import SSEHandler

        handler = SSEHandler(
            send_cb=lambda x: None,
            loop=None,
            session_id="s-1",
            task_id="t-1",
            invocation_id=None,
            mode="direct",
        )
        event = SkillHitEvent(source="MatMaster", skill_name="bohrium-job")
        assert handler._should_skip(event) is True
