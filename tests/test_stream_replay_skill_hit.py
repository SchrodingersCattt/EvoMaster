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

    def test_should_not_emit_compact_boundary(self) -> None:
        """Checkpoint boundary markers are internal-only and must stay out of replay SSE."""
        from src.services.stream_service import _should_emit_event_to_sse

        event = {
            "type": "compact_boundary",
            "source": "System",
            "content": {"covered_until_event_id": 12, "reason": "summary"},
        }
        assert _should_emit_event_to_sse(event) is False

    def test_should_not_emit_history_checkpoint(self) -> None:
        """Checkpoint payloads are restore metadata and must not leak to replay SSE."""
        from src.services.stream_service import _should_emit_event_to_sse

        event = {
            "type": "history_checkpoint",
            "source": "System",
            "content": {
                "covered_until_event_id": 12,
                "base_messages": [{"role": "user", "content": "hello"}],
            },
        }
        assert _should_emit_event_to_sse(event) is False


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

    def test_response_figures_between_response_and_run_result_keeps_dedupe(
        self,
    ) -> None:
        from src.services.stream_service import _dedupe_replayed_terminal_events

        events = [
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "response",
                "source": "MatMaster",
                "content": "answer",
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "response_figures",
                "source": "System",
                "content": {
                    "figures": [
                        {
                            "figure_id": "band",
                            "asset_url": "https://oss.example/band.png",
                            "caption": "band",
                        }
                    ]
                },
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "run_result",
                "source": "MatMaster",
                "content": "answer",
            },
        ]

        out = _dedupe_replayed_terminal_events(events)
        assert [e["type"] for e in out] == ["response", "response_figures"]


class TestReplayCompactionNormalization:
    def test_replay_converts_orphan_compaction_running_to_interrupted(self) -> None:
        from src.services.stream_service import _normalize_replayed_compaction_events

        events = [
            {
                "type": "compaction",
                "task_id": "t1",
                "spawn_id": None,
                "content": {
                    "compaction_id": "t1:root:1",
                    "status": "running",
                    "phase": "runtime",
                },
            }
        ]

        out = _normalize_replayed_compaction_events(events)

        assert out[0]["type"] == "compaction"
        assert out[0]["content"]["status"] == "interrupted"

    def test_replay_keeps_closed_compaction_lifecycle(self) -> None:
        from src.services.stream_service import _normalize_replayed_compaction_events

        events = [
            {
                "type": "compaction",
                "task_id": "t1",
                "spawn_id": None,
                "content": {
                    "compaction_id": "t1:root:1",
                    "status": "running",
                    "phase": "runtime",
                },
            },
            {
                "type": "compaction",
                "task_id": "t1",
                "spawn_id": None,
                "content": {
                    "compaction_id": "t1:root:1",
                    "status": "complete",
                    "phase": "runtime",
                    "strategy": "summary",
                },
            },
        ]

        out = _normalize_replayed_compaction_events(events)

        assert [e["content"]["status"] for e in out] == ["running", "complete"]

    def test_replay_skips_legacy_context_compaction_rows(self) -> None:
        from src.services.stream_service import _normalize_replayed_compaction_events

        events = [
            {
                "type": "context_compaction",
                "task_id": "t1",
                "spawn_id": None,
                "content": {"phase": "runtime"},
            }
        ]

        assert _normalize_replayed_compaction_events(events) == []

    def test_replay_allows_public_compaction_to_reach_sse(self) -> None:
        from src.services.stream_service import _should_emit_event_to_sse

        event = {
            "type": "compaction",
            "content": {"compaction_id": "t1:root:1", "status": "complete"},
        }

        assert _should_emit_event_to_sse(event) is True
