"""Policy-specific behavior for the Bohrium Node lease manager."""

import pytest

from src.services.bohrium_node_lifecycle import NodeIdentity
from tests.services.test_bohrium_node_lifecycle import _manager


def test_idle_timeout_last_release_keeps_node_until_deadline():
    manager, nodes, _leases, provider = _manager()
    identity = NodeIdentity("u1", "o1", 99, 456)
    handle = manager.acquire(
        identity,
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
        lifecycle_policy="idle_timeout",
        idle_timeout_seconds=1800,
    )

    assert manager.release(handle, access_key="ak", creator_id=1) is False
    assert provider.stop_count == 0
    assert nodes.row["state"] == "idle"
    assert nodes.row["idle_timeout_seconds"] == 1800
    assert nodes.row["idle_expires_at"] is not None


def test_keep_running_last_release_has_no_automatic_deadline():
    manager, nodes, _leases, provider = _manager()
    handle = manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
        lifecycle_policy="keep_running",
    )

    assert manager.release(handle, access_key="ak", creator_id=1) is False
    assert provider.stop_count == 0
    assert nodes.row["state"] == "idle"
    assert nodes.row["idle_expires_at"] is None


def test_due_idle_timeout_stops_node_and_clears_deadline():
    manager, nodes, _leases, provider = _manager()
    identity = NodeIdentity("u1", "o1", 99, 456)
    handle = manager.acquire(
        identity,
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        lifecycle_policy="idle_timeout",
        idle_timeout_seconds=900,
    )
    manager.release(handle, access_key="ak")
    candidate = dict(nodes.row)

    assert manager.stop_due_idle(candidate, access_key="ak") is True
    assert provider.stop_count == 1
    assert nodes.row["state"] == "paused"
    assert nodes.row["idle_expires_at"] is None


def test_stale_idle_scan_cannot_stop_a_reacquired_node():
    manager, nodes, _leases, provider = _manager()
    identity = NodeIdentity("u1", "o1", 99, 456)
    first = manager.acquire(
        identity,
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        lifecycle_policy="idle_timeout",
        idle_timeout_seconds=900,
    )
    manager.release(first, access_key="ak")
    stale_candidate = dict(nodes.row)
    manager.acquire(
        identity,
        session_id="session-2",
        invocation_id="inv-2",
        access_key="ak",
        lifecycle_policy="keep_running",
    )

    assert manager.stop_due_idle(stale_candidate, access_key="ak") is False
    assert provider.stop_count == 0
    assert nodes.row["state"] == "ready"


def test_idle_node_is_reused_and_its_deadline_is_cancelled():
    manager, nodes, _leases, provider = _manager()
    identity = NodeIdentity("u1", "o1", 99, 456)
    first = manager.acquire(
        identity,
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
        lifecycle_policy="idle_timeout",
        idle_timeout_seconds=900,
    )
    manager.release(first, access_key="ak", creator_id=1)

    second = manager.acquire(
        identity,
        session_id="session-2",
        invocation_id="inv-2",
        access_key="ak",
        creator_id=1,
        lifecycle_policy="run_end",
    )

    assert second.node_id == first.node_id
    assert provider.create_count == 1
    assert nodes.row["state"] == "ready"
    assert nodes.row["lifecycle_policy"] == "run_end"
    assert nodes.row["idle_expires_at"] is None


def test_latest_concurrent_acquire_policy_controls_last_release():
    manager, nodes, _leases, provider = _manager()
    identity = NodeIdentity("u1", "o1", 99, 456)
    first = manager.acquire(
        identity,
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        lifecycle_policy="keep_running",
    )
    second = manager.acquire(
        identity,
        session_id="session-2",
        invocation_id="inv-2",
        access_key="ak",
        lifecycle_policy="run_end",
    )

    assert manager.release(second, access_key="ak") is False
    assert manager.release(first, access_key="ak") is True
    assert provider.stop_count == 1
    assert nodes.row["state"] == "paused"


def test_last_release_does_not_stop_an_expired_lease_that_renews_during_cleanup():
    manager, nodes, leases, provider = _manager()
    handle = manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
    )
    leases.acquire(1, "session-2", "inv-2", "token-2", 60)
    leases.rows["inv-2"]["expired"] = True
    leases.renew_expired_before_delete = True

    assert manager.release(handle, access_key="ak") is False
    assert leases.count_for_slot(1) == 1
    assert provider.stop_count == 0
    assert nodes.row["state"] == "ready"


@pytest.mark.parametrize(
    ("policy", "timeout"),
    [
        ("idle_timeout", None),
        ("idle_timeout", 60),
        ("run_end", 900),
        ("keep_running", 900),
        ("forever", None),
    ],
)
def test_acquire_rejects_invalid_lifecycle_policy(policy, timeout):
    manager, _nodes, _leases, provider = _manager()

    with pytest.raises(ValueError):
        manager.acquire(
            NodeIdentity("u1", "o1", 99, 456),
            session_id="session-1",
            invocation_id="inv-1",
            access_key="ak",
            lifecycle_policy=policy,
            idle_timeout_seconds=timeout,
        )

    assert provider.create_count == 0


def test_manual_stop_refuses_a_slot_with_a_live_lease():
    manager, nodes, _leases, provider = _manager()
    identity = NodeIdentity("u1", "o1", 99, 456)
    manager.acquire(
        identity,
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        lifecycle_policy="keep_running",
    )

    with pytest.raises(RuntimeError, match="live lease"):
        manager.manual_stop(identity, access_key="ak", creator_id=1)

    assert provider.stop_count == 0
    assert nodes.row["state"] == "ready"


def test_manual_stop_refuses_an_expired_lease_that_renews_during_cleanup():
    manager, nodes, leases, provider = _manager()
    identity = NodeIdentity("u1", "o1", 99, 456)
    manager.acquire(
        identity,
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
    )
    leases.rows["inv-1"]["expired"] = True
    leases.renew_expired_before_delete = True

    with pytest.raises(RuntimeError, match="live lease"):
        manager.manual_stop(identity, access_key="ak")

    assert provider.stop_count == 0
    assert nodes.row["state"] == "ready"
