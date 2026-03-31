"""Test that _should_emit_event_to_sse filters skill_hit on replay."""

from __future__ import annotations


class TestReplayFilterSkillHit:
    """History replay must not emit skill_hit to SSE."""

    def test_should_not_emit_skill_hit(self) -> None:
        """_should_emit_event_to_sse returns False for skill_hit events."""
        from src.services.stream_service import _should_emit_event_to_sse

        event = {
            "type": "skill_hit",
            "source": "MatMaster",
            "content": {"skill_name": "bohrium-job"},
        }
        assert _should_emit_event_to_sse(event) is False

    def test_still_emits_tool_call(self) -> None:
        """Sanity: tool_call events are still emitted."""
        from src.services.stream_service import _should_emit_event_to_sse

        event = {"type": "tool_call", "source": "MatMaster"}
        assert _should_emit_event_to_sse(event) is True


class TestReplayDedupeSpawnId:
    """Replay dedupe must key by (task_id, spawn_id) so subagent response does not hide parent run_result."""

    def test_child_response_does_not_suppress_parent_run_result(self) -> None:
        from src.services.stream_service import _dedupe_replayed_terminal_events

        events = [
            {
                "task_id": "t1",
                "spawn_id": "sub-1",
                "type": "response",
                "source": "MatMaster",
                "content": "child answer",
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "run_result",
                "source": "MatMaster",
                "content": "parent final",
            },
        ]
        out = _dedupe_replayed_terminal_events(events)
        types = [e["type"] for e in out]
        assert types == ["response", "run_result"]

    def test_same_spawn_stream_still_dedupes_run_result_after_response(self) -> None:
        """Within one (task_id, spawn_id) stream, response still hides trailing run_result."""
        from src.services.stream_service import _dedupe_replayed_terminal_events

        events = [
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "response",
                "source": "MatMaster",
                "content": "final",
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "run_result",
                "source": "MatMaster",
                "content": "dup",
            },
        ]
        out = _dedupe_replayed_terminal_events(events)
        assert [e["type"] for e in out] == ["response"]
