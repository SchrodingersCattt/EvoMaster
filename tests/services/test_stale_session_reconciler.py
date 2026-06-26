from __future__ import annotations

from src.services.stale_session_reconciler import (
    StaleSessionReconciler,
    StaleSessionReconcilerConfig,
)


class _FakeTable:
    def __init__(self, rows):
        self.rows = rows
        self.updates: list[tuple[str, str, str]] = []

    def list_stale_reconcile_candidates(self, *, limit, min_age_seconds):
        self.scan_args = (limit, min_age_seconds)
        return list(self.rows)

    def set_session_status_if_current(self, session_id, *, current_status, new_status):
        self.updates.append((session_id, current_status, new_status))
        for row in self.rows:
            if row["session_id"] == session_id and row["status"] == current_status:
                row["status"] = new_status
                return True
        return False


class _FakeRedis:
    def __init__(self, *, reserve=True, queued=None):
        self.reserve = reserve
        self.queued = set(queued or [])
        self.released: list[tuple[str, str]] = []

    def try_reserve_nx(self, key, value, ttl_sec):
        self.reserve_args = (key, value, ttl_sec)
        return self.reserve

    def release_reservation(self, key, value):
        self.released.append((key, value))
        return True

    def is_session_run_queued(self, session_id):
        return session_id in self.queued


class _FakeRegistry:
    def __init__(self, *, owners=None, alive=None):
        self.owners = dict(owners or {})
        self.alive = set(alive or [])
        self.deleted: list[str] = []

    def get_session_run_owner(self, session_id):
        return self.owners.get(session_id)

    def is_worker_alive(self, worker_id):
        return worker_id in self.alive

    def delete_session_run_owner(self, session_id):
        self.deleted.append(session_id)


class _FakeEvents:
    def __init__(self):
        self.added = []

    def get_last_user_query(self, session_id):
        return {"content": f"last query for {session_id}"}

    def add_history_event(self, session_id, payload, user_id=None):
        self.added.append((session_id, payload, user_id))


class _FakeDeployState:
    def classify_restart_reason(self, session_id):
        return (
            "deploy",
            {
                "current_version": "v2",
                "previous_version": "v1",
            },
        )


def _cfg(**overrides):
    values = {
        "enabled": True,
        "batch_size": 100,
        "min_age_seconds": 120,
        "lock_ttl_seconds": 90,
    }
    values.update(overrides)
    return StaleSessionReconcilerConfig(**values)


def _reconciler(rows, *, redis=None, registry=None, events=None, cfg=None):
    return StaleSessionReconciler(
        sessions_table=_FakeTable(rows),
        events_service=events or _FakeEvents(),
        deploy_state_service=_FakeDeployState(),
        redis=redis or _FakeRedis(),
        registry=registry or _FakeRegistry(),
        cfg=cfg or _cfg(),
    )


def test_tick_skips_when_lock_is_held():
    table = _FakeTable(
        [{"session_id": "sid-1", "status": "active", "last_task_id": "task-1"}]
    )
    redis = _FakeRedis(reserve=False)
    reconciler = StaleSessionReconciler(
        sessions_table=table,
        events_service=_FakeEvents(),
        deploy_state_service=_FakeDeployState(),
        redis=redis,
        registry=_FakeRegistry(),
        cfg=_cfg(),
    )

    summary = reconciler.tick()

    assert summary["skipped_lock"] == 1
    assert not hasattr(table, "scan_args")
    assert redis.released == []


def test_tick_keeps_live_active_and_queued_waiting_sessions():
    rows = [
        {"session_id": "active-live", "status": "active", "last_task_id": "task-a"},
        {"session_id": "waiting-queued", "status": "waiting", "last_task_id": "task-w"},
    ]
    redis = _FakeRedis(queued={"waiting-queued"})
    registry = _FakeRegistry(owners={"active-live": "worker-1"}, alive={"worker-1"})
    events = _FakeEvents()
    reconciler = _reconciler(rows, redis=redis, registry=registry, events=events)

    summary = reconciler.tick()

    assert summary["scanned"] == 2
    assert summary["skipped_live"] == 2
    assert summary["fixed_active"] == 0
    assert summary["fixed_waiting"] == 0
    assert events.added == []


def test_tick_marks_stale_active_failed_and_records_interruption():
    rows = [
        {
            "session_id": "sid-active",
            "user_id": "user-1",
            "status": "active",
            "last_task_id": "task-1",
        }
    ]
    events = _FakeEvents()
    registry = _FakeRegistry(owners={"sid-active": "dead-worker"}, alive=set())
    reconciler = _reconciler(rows, registry=registry, events=events)

    summary = reconciler.tick()

    assert summary["fixed_active"] == 1
    assert rows[0]["status"] == "failed"
    assert registry.deleted == ["sid-active"]
    assert len(events.added) == 1
    session_id, payload, user_id = events.added[0]
    assert session_id == "sid-active"
    assert user_id == "user-1"
    assert payload["type"] == "run_interrupted"
    assert payload["task_id"] == "task-1"
    assert payload["content"]["reason"] == "deploy"
    assert payload["content"]["previous_version"] == "v1"
    assert payload["content"]["treat_as_failure"] is True


def test_tick_resets_stale_waiting_to_idle_without_history_event():
    rows = [
        {
            "session_id": "sid-waiting",
            "user_id": "user-1",
            "status": "waiting",
            "last_task_id": "task-1",
        }
    ]
    events = _FakeEvents()
    registry = _FakeRegistry(owners={"sid-waiting": "dead-worker"}, alive=set())
    reconciler = _reconciler(rows, registry=registry, events=events)

    summary = reconciler.tick()

    assert summary["fixed_waiting"] == 1
    assert rows[0]["status"] == "idle"
    assert registry.deleted == ["sid-waiting"]
    assert events.added == []
