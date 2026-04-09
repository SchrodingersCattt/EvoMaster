from __future__ import annotations

from unittest.mock import AsyncMock, Mock, call


class TestHistoryCheckpointService:
    async def test_checkpoint_sink_flushes_barrier_then_writes_pair(self) -> None:
        from src.services.history_checkpoint_service import HistoryCheckpointService

        events_table = Mock()
        events_table.get_latest_scope_event_id.return_value = 42
        events_table.add_checkpoint_pair.return_value = True

        fanout = Mock()
        fanout.flush_persistence_barrier = AsyncMock()

        tracker = Mock()
        tracker.attach_mock(fanout.flush_persistence_barrier, "flush")
        tracker.attach_mock(events_table.get_latest_scope_event_id, "latest")
        tracker.attach_mock(events_table.add_checkpoint_pair, "add_pair")

        service = HistoryCheckpointService(events_table)
        sink = service.build_checkpoint_sink(
            fanout=fanout,
            session_id="s1",
            task_id="t1",
            invocation_id="i1",
            spawn_id=None,
        )

        base_messages = [
            {
                "role": "system",
                "content": "compacted summary",
            }
        ]

        await sink(
            payload={"durability": "durable", "strategy": "summary"},
            base_messages=base_messages,
        )

        assert tracker.mock_calls == [
            call.flush(),
            call.latest("s1", None),
            call.add_pair(
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
        events_table.add_checkpoint_pair.assert_not_called()
