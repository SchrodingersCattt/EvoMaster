from __future__ import annotations

from unittest.mock import AsyncMock, Mock, call

import pytest

from matmaster.types.messages import UserMessage
from src.services.history_checkpoint_codec import serialize_base_messages


def _compact_user_message(summary: str) -> UserMessage:
    return UserMessage(content=f"<compacted-history>\n{summary}\n</compacted-history>")


def _compact_base_messages(summary: str) -> list[dict]:
    return serialize_base_messages([_compact_user_message(summary)])


class TestHistoryCheckpointService:
    async def test_checkpoint_sink_flushes_barrier_then_writes_single_checkpoint(
        self,
    ) -> None:
        from src.services.history_checkpoint_service import HistoryCheckpointService

        events_table = Mock()
        events_table.get_latest_scope_event_id.return_value = 42
        events_table.add_history_checkpoint.return_value = True

        fanout = Mock()
        fanout.flush_persistence_barrier = AsyncMock()

        tracker = Mock()
        tracker.attach_mock(fanout.flush_persistence_barrier, "flush")
        tracker.attach_mock(events_table.get_latest_scope_event_id, "latest")
        tracker.attach_mock(events_table.add_history_checkpoint, "add_checkpoint")

        service = HistoryCheckpointService(events_table)
        sink = service.build_checkpoint_sink(
            fanout=fanout,
            session_id="s1",
            task_id="t1",
            invocation_id="i1",
            spawn_id=None,
        )

        base_messages = _compact_base_messages("compacted summary")

        covered_until = await sink(
            payload={"durability": "durable", "strategy": "summary"},
            base_messages=base_messages,
        )

        assert covered_until == 42
        assert tracker.mock_calls == [
            call.flush(),
            call.latest("s1", None),
            call.add_checkpoint(
                "s1",
                task_id="t1",
                invocation_id="i1",
                spawn_id=None,
                covered_until_event_id=42,
                base_messages=base_messages,
                reason="summary",
            ),
        ]

    async def test_checkpoint_sink_skips_ephemeral_results(self) -> None:
        from src.services.history_checkpoint_service import HistoryCheckpointService

        events_table = Mock()

        fanout = Mock()
        fanout.flush_persistence_barrier = AsyncMock()

        service = HistoryCheckpointService(events_table)
        sink = service.build_checkpoint_sink(
            fanout=fanout,
            session_id="s1",
            task_id="t1",
            invocation_id="i1",
            spawn_id=None,
        )

        await sink(
            payload={"durability": "ephemeral", "strategy": "summary"},
            base_messages=[
                {
                    "role": "system",
                    "content": "compacted summary",
                }
            ],
        )

        fanout.flush_persistence_barrier.assert_not_awaited()
        events_table.get_latest_scope_event_id.assert_not_called()
        events_table.add_history_checkpoint.assert_not_called()

    async def test_checkpoint_sink_uses_payload_boundary_override(self) -> None:
        from src.services.history_checkpoint_service import HistoryCheckpointService

        events_table = Mock()
        events_table.get_latest_scope_event_id.return_value = 99
        events_table.add_history_checkpoint.return_value = True
        fanout = Mock()
        fanout.flush_persistence_barrier = AsyncMock()
        sink = HistoryCheckpointService(events_table).build_checkpoint_sink(
            fanout=fanout,
            session_id="s1",
            task_id="t1",
            invocation_id="i1",
            spawn_id=None,
        )
        base_messages = _compact_base_messages("summary")

        covered = await sink(
            payload={
                "durability": "durable",
                "strategy": "summary",
                "covered_until_event_id": 41,
            },
            base_messages=base_messages,
        )

        assert covered == 41
        events_table.get_latest_scope_event_id.assert_not_called()
        events_table.add_history_checkpoint.assert_called_once()
        assert (
            events_table.add_history_checkpoint.call_args.kwargs[
                "covered_until_event_id"
            ]
            == 41
        )

    async def test_checkpoint_sink_passes_v1_payload_metadata(self) -> None:
        from src.services.history_checkpoint_service import HistoryCheckpointService

        events_table = Mock()
        events_table.add_history_checkpoint.return_value = True
        fanout = Mock()
        fanout.flush_persistence_barrier = AsyncMock()
        sink = HistoryCheckpointService(events_table).build_checkpoint_sink(
            fanout=fanout,
            session_id="s1",
            task_id="t1",
            invocation_id="i1",
            spawn_id=None,
        )
        base_messages = _compact_base_messages("summary")

        await sink(
            payload={
                "durability": "durable",
                "strategy": "summary",
                "covered_until_event_id": 41,
                "schema_version": "history_checkpoint.v1",
                "render_version": "user_context_render.v1",
                "user_instructions_text": "Use SI units.",
                "user_instructions_hash": "sha256:abc",
            },
            base_messages=base_messages,
        )

        events_table.add_history_checkpoint.assert_called_once_with(
            "s1",
            task_id="t1",
            invocation_id="i1",
            spawn_id=None,
            covered_until_event_id=41,
            base_messages=base_messages,
            reason="summary",
            schema_version="history_checkpoint.v1",
            render_version="user_context_render.v1",
            user_instructions_text="Use SI units.",
            user_instructions_hash="sha256:abc",
        )

    async def test_checkpoint_sink_rejects_v1_payload_without_explicit_boundary(
        self,
    ) -> None:
        from src.services.history_checkpoint_service import HistoryCheckpointService

        events_table = Mock()
        fanout = Mock()
        fanout.flush_persistence_barrier = AsyncMock()
        sink = HistoryCheckpointService(events_table).build_checkpoint_sink(
            fanout=fanout,
            session_id="s1",
            task_id="t1",
            invocation_id="i1",
            spawn_id=None,
        )

        with pytest.raises(
            ValueError, match="history_checkpoint.v1.*covered_until_event_id"
        ):
            await sink(
                payload={
                    "durability": "durable",
                    "strategy": "summary",
                    "schema_version": "history_checkpoint.v1",
                },
                base_messages=_compact_base_messages("summary"),
            )

        fanout.flush_persistence_barrier.assert_not_awaited()
        events_table.get_latest_scope_event_id.assert_not_called()
        events_table.add_history_checkpoint.assert_not_called()
