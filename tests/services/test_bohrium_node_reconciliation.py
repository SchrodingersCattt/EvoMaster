"""Monitor and historical reconciliation behavior for Bohrium Node slots."""

import pytest

from src.services.bohrium_node_lifecycle import (
    HistoricalNodeStopOutcome,
    NodeIdentity,
)
from src.services.bohrium_node_reconciliation import (
    BohriumNodeReconciliationService,
)
from tests.services.test_bohrium_node_lifecycle import _manager


def _reconciler_for(manager):
    return BohriumNodeReconciliationService(
        nodes_table=manager._nodes,
        leases_table=manager._leases,
        redis=manager._redis,
        node_service=manager._node_service,
        lease_manager=manager,
        config=manager._config,
    )


def test_retry_stopping_removes_slot_missing_from_provider():
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

    provider.node_detail = None
    assert (
        _reconciler_for(manager).retry_stopping(
            nodes.row, access_key="ak", creator_id=1
        )
        is True
    )
    assert nodes.row is None
    assert provider.stop_count == 0


def test_retry_stopping_reconciles_provider_stopped_to_paused():
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

    provider.node_detail = {"node_id": 171, "status": -1}
    assert (
        _reconciler_for(manager).retry_stopping(
            nodes.row, access_key="ak", creator_id=1
        )
        is True
    )
    assert nodes.row["state"] == "paused"
    assert provider.stop_count == 0


def test_historical_ready_slot_without_lease_stops_and_becomes_paused():
    manager, nodes, leases, provider = _manager()
    handle = manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
    )
    leases.release(handle.invocation_id, handle.lease_token)

    outcome = _reconciler_for(manager).stop_unleased_ready_slot(
        dict(nodes.row), access_key="ak", creator_id=1
    )

    assert outcome is HistoricalNodeStopOutcome.STOPPED_TO_PAUSED
    assert provider.stop_count == 1
    assert nodes.row["state"] == "paused"


def test_historical_already_stopped_slot_becomes_paused_without_provider_call():
    manager, nodes, leases, provider = _manager()
    handle = manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
    )
    leases.release(handle.invocation_id, handle.lease_token)

    outcome = _reconciler_for(manager).reconcile_stopped_unleased_ready_slot(
        dict(nodes.row)
    )

    assert outcome is HistoricalNodeStopOutcome.ALREADY_STOPPED_TO_PAUSED
    assert provider.stop_count == 0
    assert nodes.row["state"] == "paused"


def test_historical_already_stopped_slot_with_concurrent_lease_is_skipped():
    manager, nodes, _leases, provider = _manager()
    manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
    )

    outcome = _reconciler_for(manager).reconcile_stopped_unleased_ready_slot(
        dict(nodes.row)
    )

    assert outcome is HistoricalNodeStopOutcome.SKIPPED_CONCURRENT_LEASE
    assert provider.stop_count == 0
    assert nodes.row["state"] == "ready"


def test_historical_already_stopped_slot_changed_since_audit_is_skipped():
    manager, nodes, leases, provider = _manager()
    handle = manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
    )
    leases.release(handle.invocation_id, handle.lease_token)
    candidate = dict(nodes.row)
    nodes.row["node_id"] = 172

    outcome = _reconciler_for(manager).reconcile_stopped_unleased_ready_slot(candidate)

    assert outcome is HistoricalNodeStopOutcome.SKIPPED_SLOT_CHANGED
    assert provider.stop_count == 0
    assert nodes.row["state"] == "ready"


def test_historical_ready_slot_with_concurrent_lease_is_skipped():
    manager, nodes, _leases, provider = _manager()
    manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
    )

    outcome = _reconciler_for(manager).stop_unleased_ready_slot(
        dict(nodes.row), access_key="ak", creator_id=1
    )

    assert outcome is HistoricalNodeStopOutcome.SKIPPED_CONCURRENT_LEASE
    assert provider.stop_count == 0
    assert nodes.row["state"] == "ready"


def test_historical_slot_skips_lease_renewed_during_expired_cleanup():
    manager, nodes, leases, provider = _manager()
    handle = manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
    )
    leases.rows[handle.invocation_id]["expired"] = True
    leases.renew_expired_before_delete = True

    outcome = _reconciler_for(manager).stop_unleased_ready_slot(
        dict(nodes.row), access_key="ak", creator_id=1
    )

    assert outcome is HistoricalNodeStopOutcome.SKIPPED_CONCURRENT_LEASE
    assert leases.count_live(handle.node_slot_id) == 1
    assert provider.stop_count == 0
    assert nodes.row["state"] == "ready"


def test_historical_slot_skips_lease_crossing_deadline_after_cleanup():
    manager, nodes, leases, provider = _manager()
    manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
    )
    leases.expire_after_delete = True

    outcome = _reconciler_for(manager).stop_unleased_ready_slot(
        dict(nodes.row), access_key="ak", creator_id=1
    )

    assert outcome is HistoricalNodeStopOutcome.SKIPPED_CONCURRENT_LEASE
    assert provider.stop_count == 0
    assert nodes.row["state"] == "ready"


def test_historical_slot_changed_since_audit_is_skipped():
    manager, nodes, leases, provider = _manager()
    handle = manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
    )
    leases.release(handle.invocation_id, handle.lease_token)
    candidate = dict(nodes.row)
    nodes.row["node_id"] = 172

    outcome = _reconciler_for(manager).stop_unleased_ready_slot(
        candidate, access_key="ak", creator_id=1
    )

    assert outcome is HistoricalNodeStopOutcome.SKIPPED_SLOT_CHANGED
    assert provider.stop_count == 0
    assert nodes.row["state"] == "ready"


def test_historical_stop_timeout_keeps_stopping_and_records_error():
    manager, nodes, leases, provider = _manager()
    handle = manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
    )
    leases.release(handle.invocation_id, handle.lease_token)
    provider.stop_failures = 1

    with pytest.raises(TimeoutError, match="stop timeout"):
        _reconciler_for(manager).stop_unleased_ready_slot(
            dict(nodes.row), access_key="ak", creator_id=1
        )

    assert nodes.row["state"] == "stopping"
    assert nodes.stop_errors == ["stop timeout"]


def test_historical_provider_missing_node_removes_stale_slot():
    manager, nodes, leases, provider = _manager()
    handle = manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
    )
    leases.release(handle.invocation_id, handle.lease_token)
    provider.stop_missing = True

    outcome = _reconciler_for(manager).stop_unleased_ready_slot(
        dict(nodes.row), access_key="ak", creator_id=1
    )

    assert outcome is HistoricalNodeStopOutcome.PROVIDER_MISSING_SLOT_REMOVED
    assert nodes.row is None


def test_historical_provider_missing_does_not_delete_changed_slot():
    manager, nodes, leases, provider = _manager()
    handle = manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
    )
    leases.release(handle.invocation_id, handle.lease_token)
    provider.stop_missing = True

    def replace_slot():
        nodes.row.update({"node_id": 172, "state": "ready"})

    provider.stop_hook = replace_slot
    outcome = _reconciler_for(manager).stop_unleased_ready_slot(
        dict(nodes.row), access_key="ak", creator_id=1
    )

    assert outcome is HistoricalNodeStopOutcome.SKIPPED_SLOT_CHANGED
    assert nodes.row["node_id"] == 172


def test_historical_provider_missing_reports_already_absent_slot():
    manager, nodes, leases, provider = _manager()
    handle = manager.acquire(
        NodeIdentity("u1", "o1", 99, 456),
        session_id="session-1",
        invocation_id="inv-1",
        access_key="ak",
        creator_id=1,
    )
    leases.release(handle.invocation_id, handle.lease_token)
    provider.stop_missing = True

    def remove_slot():
        nodes.row = None

    provider.stop_hook = remove_slot
    outcome = _reconciler_for(manager).stop_unleased_ready_slot(
        dict(nodes.row), access_key="ak", creator_id=1
    )

    assert outcome is HistoricalNodeStopOutcome.PROVIDER_MISSING_SLOT_ALREADY_ABSENT
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
        _reconciler_for(manager).recycle_expired_creation(
            dict(nodes.row), access_key="ak", creator_id=1
        )
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
        _reconciler_for(manager).recycle_expired_creation(
            dict(nodes.row), access_key="", creator_id=1
        )
        is True
    )
    assert nodes.row is None
    assert provider.stop_count == 0
