from __future__ import annotations

from src.services.bohrium_node_recycler import (
    BohriumNodeRecycler,
    BohriumNodeRecyclerConfig,
)


class _Redis:
    def __init__(self, reserve=True) -> None:
        self.reserve = reserve
        self.released = []

    def try_reserve_nx(self, key, value, ttl):
        self.reserved = (key, value, ttl)
        return self.reserve

    def release_reservation(self, key, value):
        self.released.append((key, value))
        return True


class _Leases:
    def list_expired(self, limit):
        assert limit == 10
        return [
            {
                "node_slot_id": 7,
                "session_id": "s1",
                "invocation_id": "inv-expired",
                "lease_token": "token-expired",
            }
        ]


class _Nodes:
    def list_due_idle_slots(self, limit):
        assert limit == 10
        return []

    def list_expired_creating_slots(self, limit):
        assert limit == 10
        return [
            {
                "id": 9,
                "user_id": "u3",
                "org_id": "o3",
                "project_id": 101,
                "sku_id": 456,
                "node_id": 173,
                "state": "creating",
                "creating_lease_token": "stale-create-token",
            }
        ]

    def find_by_id(self, slot_id):
        assert slot_id == 7
        return {
            "id": 7,
            "user_id": "u1",
            "org_id": "o1",
            "project_id": 99,
            "sku_id": 456,
            "node_id": 171,
            "state": "ready",
        }

    def list_stopping_without_live_leases(self, limit, min_age_seconds):
        assert limit == 10
        assert min_age_seconds == 5
        return [
            {
                "id": 8,
                "user_id": "u2",
                "org_id": "o2",
                "project_id": 100,
                "sku_id": 456,
                "node_id": 172,
                "state": "stopping",
            }
        ]


class _Manager:
    def __init__(self) -> None:
        self.expired = []
        self.retried = []
        self.creating = []

    def release_expired_row(self, row, *, access_key, creator_id):
        self.expired.append((row, access_key, creator_id))
        return True

    def retry_stopping(self, row, *, access_key, creator_id):
        self.retried.append((row, access_key, creator_id))
        return True

    def recycle_expired_creation(self, row, *, access_key, creator_id):
        self.creating.append((row, access_key, creator_id))
        return True


def test_tick_retries_stopping_slots_and_releases_expired_invocations():
    redis = _Redis()
    manager = _Manager()
    recycler = BohriumNodeRecycler(
        redis=redis,
        leases_table=_Leases(),
        nodes_table=_Nodes(),
        reconciliation_service=manager,
        access_key_loader=lambda user_id, org_id: f"ak:{user_id}:{org_id}",
        config=BohriumNodeRecyclerConfig(
            batch_size=10, lock_ttl_seconds=30, stop_retry_min_age_seconds=5
        ),
    )

    summary = recycler.tick()

    assert summary["expired_scanned"] == 1
    assert summary["expired_released"] == 1
    assert summary["stopping_scanned"] == 1
    assert summary["stop_retried"] == 1
    assert summary["creating_scanned"] == 1
    assert summary["creating_recycled"] == 1
    assert summary["idle_scanned"] == 0
    assert summary["tick_failed"] == 0
    assert manager.creating[0][1] == "ak:u3:o3"
    assert manager.retried[0][1] == "ak:u2:o2"
    assert manager.expired[0][0]["invocation_id"] == "inv-expired"
    assert redis.released


def test_tick_stops_only_due_idle_timeout_slots():
    redis = _Redis()
    manager = _Manager()
    nodes = _Nodes()
    due = {
        "id": 10,
        "user_id": "4",
        "org_id": "o4",
        "project_id": 102,
        "sku_id": 456,
        "node_id": 174,
        "state": "idle",
        "lifecycle_policy": "idle_timeout",
        "idle_timeout_seconds": 900,
    }
    nodes.list_due_idle_slots = lambda limit: [due]
    manager.stop_due_idle = lambda row, *, access_key, creator_id: (
        row == due and access_key == "ak:4:o4" and creator_id == 4
    )
    recycler = BohriumNodeRecycler(
        redis=redis,
        leases_table=_Leases(),
        nodes_table=nodes,
        reconciliation_service=manager,
        access_key_loader=lambda user_id, org_id: f"ak:{user_id}:{org_id}",
        config=BohriumNodeRecyclerConfig(
            batch_size=10, lock_ttl_seconds=30, stop_retry_min_age_seconds=5
        ),
    )

    summary = recycler.tick()

    assert summary["idle_scanned"] == 1
    assert summary["idle_stopped"] == 1


def test_tick_fails_closed_when_redis_is_unavailable():
    recycler = BohriumNodeRecycler(
        redis=_Redis(reserve=None),
        leases_table=_Leases(),
        nodes_table=_Nodes(),
        reconciliation_service=_Manager(),
        access_key_loader=lambda _user_id, _org_id: "ak",
        config=BohriumNodeRecyclerConfig(
            batch_size=10, lock_ttl_seconds=30, stop_retry_min_age_seconds=5
        ),
    )

    summary = recycler.tick()

    assert summary["skipped_redis"] == 1
    assert summary["expired_scanned"] == 0
    assert summary["stopping_scanned"] == 0
    assert summary["creating_scanned"] == 0
