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
            "content": {"skill_name": "bohrium"},
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


def test_should_not_emit_user_turn_context() -> None:
    from src.services.stream_service import _should_emit_event_to_sse

    event = {
        "type": "user_turn_context",
        "source": "MatMaster",
        "content": {
            "schema_version": "user_turn_context.v1",
            "kind": "anchor",
            "message": {"role": "user", "content": "provider-facing"},
            "render_version": "user_context_render.v1",
        },
    }

    assert _should_emit_event_to_sse(event) is False


def test_normalize_replayed_event_unpacks_structured_response_content() -> None:
    from src.services.stream_service import _normalize_replayed_event

    out = _normalize_replayed_event(
        {
            "source": "agent",
            "type": "response",
            "content": {
                "content": "answer",
                "turn_index": 1,
                "turn_usage": {"total_tokens": 12},
                "total_usage": {"total_tokens": 20},
            },
            "session_id": "sess",
            "task_id": "task",
            "spawn_id": None,
        }
    )
    assert out["source"] == "MatMaster"
    assert out["content"] == "answer"
    assert out["turn_index"] == 1


def test_normalize_replayed_event_lifts_structured_run_result_content() -> None:
    from src.services.stream_service import _normalize_replayed_event

    out = _normalize_replayed_event(
        {
            "source": "agent",
            "type": "run_result",
            "content": {
                "content": "answer",
                "status": "completed",
                "reason": "natural",
                "usage": {"total_tokens": 20},
                "model": "gpt-5-mini",
            },
            "session_id": "sess",
            "task_id": "task",
            "spawn_id": None,
        }
    )
    assert out["source"] == "MatMaster"
    assert out["final_content"] == "answer"
    assert out["status"] == "completed"
    assert out["reason"] == "natural"
    assert out["usage"] == {"total_tokens": 20}
    assert out["model"] == "gpt-5-mini"


class TestReplayDedupeSpawnId:
    """Replay dedupe must key by (task_id, spawn_id) so child and parent terminal streams stay independent."""

    def test_child_response_does_not_suppress_parent_run_result(self) -> None:
        """Child text equals parent final text: isolation must come from spawn grouping."""
        from src.services.stream_service import _dedupe_replayed_terminal_events

        events = [
            {
                "task_id": "t1",
                "spawn_id": "sub-1",
                "type": "response",
                "source": "MatMaster",
                "content": "same final text",
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "run_result",
                "source": "MatMaster",
                "content": {
                    "content": "same final text",
                    "status": "completed",
                    "reason": "natural",
                },
            },
        ]
        out = _dedupe_replayed_terminal_events(events)
        types = [e["type"] for e in out]
        assert types == ["response", "run_result"]

    def test_same_spawn_stream_prefers_run_result_over_response(self) -> None:
        """Within one (task_id, spawn_id) stream, run_result is the replayed terminal answer."""
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
                "content": {
                    "content": "final",
                    "status": "completed",
                    "reason": "natural",
                },
            },
        ]
        out = _dedupe_replayed_terminal_events(events)
        assert [e["type"] for e in out] == ["run_result"]

    def test_response_figures_between_response_and_run_result_are_preserved(
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
                "content": {
                    "content": "answer",
                    "status": "completed",
                    "reason": "natural",
                },
            },
        ]

        out = _dedupe_replayed_terminal_events(events)
        assert [e["type"] for e in out] == ["response_figures", "run_result"]

    def test_response_figures_before_response_are_preserved_with_run_result(
        self,
    ) -> None:
        from src.services.stream_service import _dedupe_replayed_terminal_events

        events = [
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
                "type": "response",
                "source": "MatMaster",
                "content": "answer with [[fig:band]]",
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "run_result",
                "source": "MatMaster",
                "content": {
                    "content": "answer with [[fig:band]]",
                    "status": "completed",
                    "reason": "natural",
                },
            },
        ]

        out = _dedupe_replayed_terminal_events(events)
        assert [e["type"] for e in out] == ["response_figures", "run_result"]

    def test_interleaved_response_and_multiple_response_figures_keep_order(
        self,
    ) -> None:
        from src.services.stream_service import _dedupe_replayed_terminal_events

        events = [
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
                "type": "response",
                "source": "MatMaster",
                "content": "first answer chunk",
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
                        },
                        {
                            "figure_id": "dos",
                            "asset_url": "https://oss.example/dos.png",
                            "caption": "dos",
                        },
                    ]
                },
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "run_result",
                "source": "MatMaster",
                "content": {
                    "content": "duplicate final answer",
                    "status": "completed",
                    "reason": "natural",
                },
            },
        ]

        out = _dedupe_replayed_terminal_events(events)
        assert [e["type"] for e in out] == [
            "response_figures",
            "response",
            "response_figures",
            "run_result",
        ]


class TestReplayDedupeFinalAnswer:
    """Replay dedupe removes only the run_result's final-answer response copy."""

    def test_final_response_with_matching_text_is_removed(self) -> None:
        """Spec 8.1: the equivalent final answer copy stays hidden behind run_result."""
        from src.services.stream_service import _dedupe_replayed_terminal_events

        events = [
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "response",
                "source": "MatMaster",
                "content": {"content": "final", "turn_index": 0},
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "run_result",
                "source": "MatMaster",
                "content": {
                    "content": "final",
                    "status": "completed",
                    "reason": "natural",
                    "num_turns": 1,
                },
            },
        ]

        out = _dedupe_replayed_terminal_events(events)
        assert [e["type"] for e in out] == ["run_result"]

    def test_intermediate_response_before_tool_call_is_kept(self) -> None:
        """Spec 8.2: intermediate visible text must survive replay after the run ends."""
        from src.services.stream_service import _dedupe_replayed_terminal_events

        events = [
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "response",
                "source": "MatMaster",
                "content": {"content": "I will inspect files.", "turn_index": 0},
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "tool_call",
                "source": "MatMaster",
                "content": {"name": "bash"},
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "tool_result",
                "source": "MatMaster",
                "content": {"result": "ok"},
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "response",
                "source": "MatMaster",
                "content": {"content": "final", "turn_index": 1},
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "run_result",
                "source": "MatMaster",
                "content": {
                    "content": "final",
                    "status": "completed",
                    "reason": "natural",
                    "num_turns": 2,
                },
            },
        ]

        out = _dedupe_replayed_terminal_events(events)
        assert [e["type"] for e in out] == [
            "response",
            "tool_call",
            "tool_result",
            "run_result",
        ]
        assert out[0]["content"]["content"] == "I will inspect files."

    def test_response_with_different_text_is_kept(self) -> None:
        """Spec 6.3: only the equivalent final-answer copy is removed."""
        from src.services.stream_service import _dedupe_replayed_terminal_events

        events = [
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "response",
                "source": "MatMaster",
                "content": {"content": "partial draft", "turn_index": 1},
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "run_result",
                "source": "MatMaster",
                "content": {
                    "content": "final conclusion",
                    "status": "completed",
                    "reason": "natural",
                    "num_turns": 2,
                },
            },
        ]

        out = _dedupe_replayed_terminal_events(events)
        assert [e["type"] for e in out] == ["response", "run_result"]

    def test_rescued_finish_removes_earlier_turn_final_copy(self) -> None:
        """Spec 6.5: final_content may come from an earlier turn (rescued natural finish)."""
        from src.services.stream_service import _dedupe_replayed_terminal_events

        events = [
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "response",
                "source": "MatMaster",
                "content": {"content": "conclusion X", "turn_index": 0},
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "tool_call",
                "source": "MatMaster",
                "content": {"name": "bash"},
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "tool_result",
                "source": "MatMaster",
                "content": {"result": "ok"},
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "run_result",
                "source": "MatMaster",
                "content": {
                    "content": "conclusion X",
                    "status": "completed",
                    "reason": "natural",
                    "num_turns": 2,
                },
            },
        ]

        out = _dedupe_replayed_terminal_events(events)
        assert [e["type"] for e in out] == ["tool_call", "tool_result", "run_result"]

    def test_empty_terminal_text_removes_nothing(self) -> None:
        """Spec 6.6: cancelled-style run_result persists '' and must not hide drafts."""
        from src.services.stream_service import _dedupe_replayed_terminal_events

        events = [
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "response",
                "source": "MatMaster",
                "content": {"content": "partial draft", "turn_index": 0},
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "run_result",
                "source": "MatMaster",
                "content": {
                    "content": "",
                    "status": "failed",
                    "reason": "cancelled",
                },
            },
        ]

        out = _dedupe_replayed_terminal_events(events)
        assert [e["type"] for e in out] == ["response", "run_result"]

    def test_str_form_response_content_participates_in_text_match(self) -> None:
        """Spec 5.4: plain-str response content is a legal persisted shape, not legacy."""
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
                "content": {
                    "content": "final",
                    "status": "completed",
                    "reason": "natural",
                },
            },
        ]

        out = _dedupe_replayed_terminal_events(events)
        assert [e["type"] for e in out] == ["run_result"]

    def test_each_run_result_removes_at_most_one_response(self) -> None:
        """Spec 9.3: anomalous duplicate writes must not be over-deleted."""
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
                "type": "response",
                "source": "MatMaster",
                "content": "final",
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "run_result",
                "source": "MatMaster",
                "content": {
                    "content": "final",
                    "status": "completed",
                    "reason": "natural",
                },
            },
        ]

        out = _dedupe_replayed_terminal_events(events)
        assert [e["type"] for e in out] == ["response", "run_result"]

    def test_multiple_run_results_each_remove_one_copy(self) -> None:
        """Spec 9.3: with N terminals, at most N matching copies disappear."""
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
                "type": "response",
                "source": "MatMaster",
                "content": "final",
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "run_result",
                "source": "MatMaster",
                "content": {
                    "content": "final",
                    "status": "completed",
                    "reason": "natural",
                },
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "run_result",
                "source": "MatMaster",
                "content": {
                    "content": "final",
                    "status": "completed",
                    "reason": "natural",
                },
            },
        ]

        out = _dedupe_replayed_terminal_events(events)
        assert [e["type"] for e in out] == ["run_result", "run_result"]


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
