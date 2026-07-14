from __future__ import annotations

import threading
import time
from dataclasses import replace

import pytest

from src.services.bohrium_node_lifecycle import (
    BohriumNodeLeaseManager,
    NodeIdentity,
    NodeLeaseConfig,
    NodeLeaseHeartbeat,
)
from src.services.bohrium_node_service import BohriumNodeNotFoundError


class _RedisLock:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._lock = threading.Lock()

    def try_reserve_nx(self, key, value, _ttl):
        with self._lock:
            if key in self._values:
                return False
            self._values[key] = value
            return True

    def release_reservation(self, key, value):
        with self._lock:
            if self._values.get(key) != value:
                return False
            del self._values[key]
            return True


class _Nodes:
    def __init__(self, events=None) -> None:
        self.row = None
        self.attached_node_ids = []
        self.events = events if events is not None else []
        self._next_id = 1
        self._lock = threading.Lock()

    def find_one_for_reuse(self, user_id, org_id, project_id, sku_id):
        del user_id, org_id, project_id, sku_id
        with self._lock:
            return dict(self.row) if self.row else None

    def find_by_id(self, slot_id):
        with self._lock:
            if not self.row or self.row["id"] != slot_id:
                return None
            return dict(self.row)

    def insert_creating_slot(
        self, user_id, org_id, project_id, sku_id, invocation_id, token, _ttl
    ):
        with self._lock:
            if self.row:
                return False
            self.row = {
                "id": self._next_id,
                "user_id": user_id,
                "org_id": org_id,
                "project_id": project_id,
                "sku_id": sku_id,
                "node_id": None,
                "state": "creating",
                "creating_invocation_id": invocation_id,
                "creating_lease_token": token,
                "creating_expired": False,
                "lifecycle_policy": "run_end",
            }
            return True

    def claim_expired_creation(self, slot_id, invocation_id, token, _ttl):
        with self._lock:
            if (
                not self.row
                or self.row["id"] != slot_id
                or self.row["state"] != "creating"
                or not self.row["creating_expired"]
            ):
                return False
            self.row["creating_invocation_id"] = invocation_id
            self.row["creating_lease_token"] = token
            self.row["creating_expired"] = False
            return True

    def begin_restart(self, slot_id, node_id, invocation_id, token, _ttl):
        with self._lock:
            if (
                not self.row
                or self.row["id"] != slot_id
                or self.row["node_id"] != node_id
                or self.row["state"] not in {"paused", "ready"}
            ):
                return False
            self.row["state"] = "creating"
            self.row["creating_invocation_id"] = invocation_id
            self.row["creating_lease_token"] = token
            return True

    def mark_ready(self, slot_id, token, node_id):
        with self._lock:
            if (
                not self.row
                or self.row["id"] != slot_id
                or self.row["state"] != "creating"
                or self.row["creating_lease_token"] != token
            ):
                return False
            self.events.append("ready")
            self.row["node_id"] = node_id
            self.row["state"] = "ready"
            self.row["creating_invocation_id"] = None
            self.row["creating_lease_token"] = None
            return True

    def attach_creating_node(self, slot_id, token, node_id):
        with self._lock:
            if (
                not self.row
                or self.row["id"] != slot_id
                or self.row["state"] != "creating"
                or self.row["creating_lease_token"] != token
            ):
                return False
            self.row["node_id"] = node_id
            self.attached_node_ids.append(node_id)
            return True

    def expire_creation(self, slot_id, token, _error):
        with self._lock:
            if self.row and self.row["id"] == slot_id:
                if self.row["creating_lease_token"] == token:
                    self.row["creating_expired"] = True
                    return True
            return False

    def mark_stopping(self, slot_id, node_id):
        with self._lock:
            if (
                not self.row
                or self.row["id"] != slot_id
                or self.row["node_id"] != node_id
                or self.row["state"] != "ready"
            ):
                return False
            self.row["state"] = "stopping"
            return True

    def mark_stopping_expired_creation(self, slot_id, node_id, token):
        with self._lock:
            if (
                not self.row
                or self.row["id"] != slot_id
                or self.row["node_id"] != node_id
                or self.row["state"] != "creating"
                or self.row["creating_lease_token"] != token
                or not self.row["creating_expired"]
            ):
                return False
            self.row["state"] = "stopping"
            return True

    def delete_expired_empty_creation(self, slot_id, token):
        with self._lock:
            if (
                not self.row
                or self.row["id"] != slot_id
                or self.row["node_id"] is not None
                or self.row["state"] != "creating"
                or self.row["creating_lease_token"] != token
                or not self.row["creating_expired"]
            ):
                return False
            self.row = None
            return True

    def mark_paused(self, slot_id, node_id):
        with self._lock:
            if (
                not self.row
                or self.row["id"] != slot_id
                or self.row["node_id"] != node_id
                or self.row["state"] != "stopping"
            ):
                return False
            self.row["state"] = "paused"
            return True

    def record_stop_error(self, slot_id, node_id, _error):
        with self._lock:
            return bool(
                self.row
                and self.row["id"] == slot_id
                and self.row["node_id"] == node_id
            )

    def update_last_used_at(self, *_args):
        return True

    def delete_by_node(self, user_id, org_id, project_id, sku_id, node_id):
        with self._lock:
            if not self.row:
                return False
            assert (
                user_id,
                org_id,
                project_id,
                sku_id,
                node_id,
            ) == ("u1", "o1", 99, 456, 171)
            self.row = None
            return True


class _Leases:
    def __init__(self, events=None) -> None:
        self.rows: dict[str, dict] = {}
        self.events = events if events is not None else []
        self._lock = threading.Lock()

    def acquire(self, slot_id, session_id, invocation_id, token, _ttl):
        with self._lock:
            self.events.append("lease")
            self.rows[invocation_id] = {
                "slot_id": slot_id,
                "session_id": session_id,
                "token": token,
                "live": True,
                "expired": False,
            }
            return True

    def heartbeat(self, invocation_id, token, _ttl):
        with self._lock:
            row = self.rows.get(invocation_id)
            return bool(row and row["token"] == token and row["live"])

    def release(self, invocation_id, token):
        with self._lock:
            row = self.rows.get(invocation_id)
            if not row or row["token"] != token or not row["live"]:
                return False
            row["live"] = False
            return True

    def release_expired(self, invocation_id, token):
        with self._lock:
            row = self.rows.get(invocation_id)
            if (
                not row
                or row["token"] != token
                or not row["live"]
                or not row["expired"]
            ):
                return False
            row["live"] = False
            return True

    def count_live(self, slot_id):
        with self._lock:
            return sum(
                1
                for row in self.rows.values()
                if row["slot_id"] == slot_id and row["live"]
            )


class _Provider:
    def __init__(self) -> None:
        self.create_count = 0
        self.stop_count = 0
        self.restart_count = 0
        self.stop_failures = 0
        self.stop_missing = False
        self._lock = threading.Lock()

    def create_node(self, _access_key, _project_id, *, sku_id):
        assert sku_id == 456
        with self._lock:
            self.create_count += 1
        time.sleep(0.02)
        return {"node_id": 171}

    def wait_until_ready(self, _access_key, node_id):
        return {"node_id": node_id, "ip": "10.0.0.1", "password": "pwd"}

    def get_node_info(self, _access_key, node_id):
        return {"node_id": node_id, "ip": "10.0.0.1", "password": "pwd"}

    def restart_node(self, *_args, **_kwargs):
        with self._lock:
            self.restart_count += 1

    def destroy_node(self, *_args, **_kwargs):
        return None

    def stop_node(self, *_args, **_kwargs):
        with self._lock:
            if self.stop_missing:
                raise BohriumNodeNotFoundError("node missing")
            if self.stop_failures:
                self.stop_failures -= 1
                raise TimeoutError("stop timeout")
            self.stop_count += 1


def _manager():
    events = []
    nodes = _Nodes(events)
    leases = _Leases(events)
    provider = _Provider()
    manager = BohriumNodeLeaseManager(
        nodes_table=nodes,
        leases_table=leases,
        redis=_RedisLock(),
        node_service=provider,
        config=NodeLeaseConfig(
            lease_ttl_seconds=60,
            creation_ttl_seconds=60,
            slot_lock_ttl_seconds=5,
            acquire_timeout_seconds=2,
            retry_interval_seconds=0.001,
        ),
    )
    return manager, nodes, leases, provider


def test_default_acquire_timeout_outlives_creation_claim():
    config = NodeLeaseConfig()

    assert config.acquire_timeout_seconds > config.creation_ttl_seconds


def test_concurrent_invocations_share_one_node_and_last_release_stops_it():
    manager, nodes, leases, provider = _manager()
    identity = NodeIdentity("u1", "o1", 99, 456)
    handles = []

    def acquire(invocation_id):
        handles.append(
            manager.acquire(
                identity,
                session_id=f"session-{invocation_id}",
                invocation_id=invocation_id,
                access_key="ak",
                creator_id=1,
            )
        )

    threads = [
        threading.Thread(target=acquire, args=("inv-1",)),
        threading.Thread(target=acquire, args=("inv-2",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert provider.create_count == 1
    assert nodes.attached_node_ids == [171]
    assert nodes.events.index("lease") < nodes.events.index("ready")
    assert {handle.node_id for handle in handles} == {171}
    assert leases.count_live(1) == 2

    assert manager.release(handles[0], access_key="ak", creator_id=1) is False
    assert provider.stop_count == 0
    assert nodes.row["state"] == "ready"

    assert manager.release(handles[1], access_key="ak", creator_id=1) is True
    assert provider.stop_count == 1
    assert nodes.row["state"] == "paused"


def test_stale_token_cannot_release_retried_invocation_or_stop_node():
    manager, _nodes, leases, provider = _manager()
    handle = manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
    )
    leases.acquire(1, "session-1", "inv-1", "new-token", 60)

    assert manager.release(handle, access_key="ak", creator_id=1) is False
    assert provider.stop_count == 0
    assert manager.heartbeat(handle) is False

    new_handle = replace(handle, lease_token="new-token")
    assert manager.release(new_handle, access_key="ak", creator_id=1) is True
    assert provider.stop_count == 1


def test_heartbeat_runs_until_stopped_and_does_not_outlive_run():
    manager, _nodes, _leases, _provider = _manager()
    handle = manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
    )
    calls = 0
    original = manager.heartbeat

    def counted(lease):
        nonlocal calls
        calls += 1
        return original(lease)

    manager.heartbeat = counted
    heartbeat = NodeLeaseHeartbeat(manager, handle, interval_seconds=0.001)

    heartbeat.start()
    deadline = time.monotonic() + 1
    while calls < 2 and time.monotonic() < deadline:
        time.sleep(0.001)
    heartbeat.stop()
    stopped_at = calls
    time.sleep(0.005)

    assert stopped_at >= 2
    assert calls == stopped_at


def test_recycler_cannot_release_lease_renewed_after_candidate_scan():
    manager, _nodes, leases, provider = _manager()
    handle = manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
    )
    leases.rows["inv-1"]["expired"] = False

    assert manager.release_expired(handle, access_key="ak", creator_id=1) is None
    assert leases.count_live(1) == 1
    assert provider.stop_count == 0


def test_stop_timeout_keeps_stopping_state_for_monitor_retry():
    manager, nodes, _leases, provider = _manager()
    handle = manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
    )
    provider.stop_failures = 1

    with pytest.raises(TimeoutError, match="stop timeout"):
        manager.release(handle, access_key="ak", creator_id=1)

    assert nodes.row["state"] == "stopping"
    assert manager.retry_stopping(nodes.row, access_key="ak", creator_id=1) is True
    assert nodes.row["state"] == "paused"
    assert provider.stop_count == 1


def test_provider_deleted_node_removes_stale_slot_after_last_release():
    manager, nodes, _leases, provider = _manager()
    handle = manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
    )
    provider.stop_missing = True

    assert manager.release(handle, access_key="ak", creator_id=1) is True
    assert nodes.row is None


def test_recycler_stops_node_left_in_expired_creating_state():
    manager, nodes, leases, provider = _manager()
    handle = manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
    )
    leases.release(handle.invocation_id, handle.lease_token)
    nodes.row.update(
        {
            "state": "creating",
            "creating_lease_token": "stale-create-token",
            "creating_expired": True,
        }
    )

    assert (
        manager.recycle_expired_creation(dict(nodes.row), access_key="ak", creator_id=1)
        is True
    )
    assert provider.stop_count == 1
    assert nodes.row["state"] == "paused"


def test_recycler_deletes_expired_creating_placeholder_without_node():
    manager, nodes, _leases, provider = _manager()
    identity = NodeIdentity("u1", "o1", 99, 456)
    nodes.insert_creating_slot(
        identity.user_id,
        identity.org_id,
        identity.project_id,
        identity.sku_id,
        "inv-1",
        "stale-create-token",
        60,
    )
    nodes.row["creating_expired"] = True

    assert (
        manager.recycle_expired_creation(dict(nodes.row), access_key="", creator_id=1)
        is True
    )
    assert nodes.row is None
    assert provider.stop_count == 0
