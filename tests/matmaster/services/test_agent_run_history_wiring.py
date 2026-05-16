from __future__ import annotations

from unittest.mock import patch

from src.services.agent_run_history_wiring import build_history_wiring


def _build_history_wiring(*, events_table):
    return build_history_wiring(
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
