from __future__ import annotations

from unittest.mock import patch

import pytest

from matmaster.context.ports import SessionEventQuery
from matmaster.types.figures import FigureUploadConfig
from matmaster.types.runtime_ports import FigureUploadPort, PlaygroundRuntimePorts
from src.services.agent_run_history_wiring import build_history_wiring


def _build_history_wiring(
    *,
    events_table,
    base_runtime_ports: PlaygroundRuntimePorts | None = None,
):
    return build_history_wiring(
        base_runtime_ports=base_runtime_ports or PlaygroundRuntimePorts(),
        events_table=events_table,
        session_id="sess-1",
        task_id="task-1",
        raw_history_limit=10,
        child_event_sink=lambda event: None,
        checkpoint_sink_factory=lambda **kwargs: (lambda **inner: None),
        pre_compaction_barrier=lambda: None,
    )


def test_history_wiring_without_events_table_has_no_scope_boundary() -> None:
    result = _build_history_wiring(events_table=None)

    history = result.runtime_ports.compaction.history
    assert history is not None
    assert history.latest_scope_event_id() is None


def test_history_wiring_none_scope_boundary_stays_missing() -> None:
    class EventsTable:
        def get_session_user_query_events(self, session_id):
            return []

        def get_latest_scope_event_id(self, session_id, spawn_id):
            return None

        def get_bohrium_events(self, session_id):
            return []

    with patch(
        "src.services.agent_run_history_wiring.ModelHistoryRestoreService"
    ) as restore_cls:
        restore_cls.return_value.restore_history.return_value = []
        result = _build_history_wiring(events_table=EventsTable())

    history = result.runtime_ports.compaction.history
    assert history is not None
    assert history.latest_scope_event_id() is None


@pytest.mark.asyncio
async def test_history_wiring_load_events_returns_typed_session_events() -> None:
    class EventsTable:
        def get_session_user_query_events(self, session_id):
            return []

        def query_context_events(self, **kwargs):
            self.context_kwargs = kwargs
            return [
                {
                    "id": "3",
                    "source": " User ",
                    "type": " query ",
                    "content": {"content": "old", "files": ["a"]},
                    "created_at_ms": 100,
                }
            ]

        def get_latest_scope_event_id(self, session_id, spawn_id):
            return 3

        def get_bohrium_events(self, session_id):
            return []

    table = EventsTable()

    with patch(
        "src.services.agent_run_history_wiring.ModelHistoryRestoreService"
    ) as restore_cls:
        restore_cls.return_value.restore_history.return_value = []
        result = _build_history_wiring(events_table=table)

    history = result.runtime_ports.compaction.history
    assert history is not None
    events = await history.load_events(
        SessionEventQuery(session_id="sess-1", spawn_id=None, until_event_id=3)
    )

    assert events[0].id == 3
    assert events[0].source == "User"
    assert events[0].content["files"] == ("a",)
    assert table.context_kwargs["session_id"] == "sess-1"


def test_build_history_wiring_merges_into_existing_runtime_ports() -> None:
    cfg = FigureUploadConfig(
        session_id="sess-1",
        task_id="task-1",
        asset_key_prefix="figures/sess-1/task-1",
        upload_bytes=lambda data, name: f"https://oss.example/{name}",
    )
    base_ports = PlaygroundRuntimePorts(
        figure_upload=FigureUploadPort(config=cfg)
    )

    result = _build_history_wiring(
        events_table=None,
        base_runtime_ports=base_ports,
    )

    assert result.runtime_ports.figure_upload.config is cfg
    assert result.runtime_ports.child_event_forward_sink is not None
    assert result.runtime_ports.compaction.history is not None
