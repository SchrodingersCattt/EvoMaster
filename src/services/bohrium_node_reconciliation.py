"""Monitor and audit reconciliation for persisted Bohrium Node slots."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from src.dao.bohrium_node_leases_table import get_bohrium_node_leases_table
from src.dao.bohrium_nodes_table import get_bohrium_nodes_table
from src.dao.redis_dao import get_redis_dao
from src.services.bohrium_node_contract import (
    HistoricalNodeStopOutcome,
    NodeIdentity,
    NodeLease,
    NodeLeaseConfig,
)
from src.services.bohrium_node_coordination import (
    has_leases_after_expired_cleanup,
    node_slot_lock,
)
from src.services.bohrium_node_service import (
    NODE_STATUS_STOPPED,
    BohriumNodeNotFoundError,
    get_bohrium_node_service,
)


class BohriumNodeReconciliationService:
    """Reconcile monitor and audit candidates without owning the live run path."""

    def __init__(
        self,
        *,
        nodes_table: Any | None = None,
        leases_table: Any | None = None,
        redis: Any | None = None,
        node_service: Any | None = None,
        lease_manager: Any | None = None,
        config: NodeLeaseConfig | None = None,
    ) -> None:
        self._nodes = nodes_table or get_bohrium_nodes_table()
        self._leases = leases_table or get_bohrium_node_leases_table()
        self._redis = redis or get_redis_dao()
        self._node_service = node_service or get_bohrium_node_service()
        self._config = config or NodeLeaseConfig.from_env()
        if lease_manager is None:
            from src.services.bohrium_node_lifecycle import BohriumNodeLeaseManager

            lease_manager = BohriumNodeLeaseManager(
                nodes_table=self._nodes,
                leases_table=self._leases,
                redis=self._redis,
                node_service=self._node_service,
                config=self._config,
            )
        self._lease_manager = lease_manager

    def _slot_lock(self, identity: NodeIdentity):
        return node_slot_lock(self._redis, identity, self._config)

    def _has_leases_after_expired_cleanup(self, slot_id: int) -> bool:
        return has_leases_after_expired_cleanup(self._leases, slot_id)

    def stop_due_idle(
        self,
        row: dict[str, Any],
        *,
        access_key: str,
        creator_id: int = 0,
    ) -> bool:
        """Stop a due idle_timeout candidate after a locked CAS recheck."""
        identity = _identity_from_row(row)
        slot_id = int(row["id"])
        node_id = int(row["node_id"])
        with self._slot_lock(identity):
            current = self._nodes.find_by_id(slot_id)
            if (
                not current
                or current.get("state") != "idle"
                or current.get("node_id") is None
                or int(current["node_id"]) != node_id
                or self._has_leases_after_expired_cleanup(slot_id)
                or not self._nodes.mark_stopping_due_idle(slot_id, node_id)
            ):
                return False
        return self._stop_claimed_slot(
            identity,
            slot_id,
            node_id,
            access_key=access_key,
            creator_id=creator_id,
        )

    def _stop_claimed_slot(
        self,
        identity: NodeIdentity,
        slot_id: int,
        node_id: int,
        *,
        access_key: str,
        creator_id: int,
    ) -> bool:
        try:
            self._node_service.stop_node(
                access_key,
                node_id,
                identity.project_id,
                creator_id=creator_id,
            )
        except BohriumNodeNotFoundError:
            return self._nodes.delete_by_node(
                identity.user_id,
                identity.org_id,
                identity.project_id,
                identity.sku_id,
                node_id,
            )
        except Exception as exc:
            self._nodes.record_stop_error(slot_id, node_id, str(exc))
            raise
        with self._slot_lock(identity):
            if not self._nodes.mark_paused(slot_id, node_id):
                raise RuntimeError("Bohrium node stop state was fenced")
        return True

    def release_expired_row(
        self,
        row: dict[str, Any],
        *,
        access_key: str,
        creator_id: int = 0,
    ) -> bool | None:
        """Adapt a joined slot/lease recycler row into a fenced release."""
        lease = NodeLease(
            identity=_identity_from_row(row),
            node_slot_id=int(row["node_slot_id"]),
            node_id=int(row["node_id"]),
            session_id=str(row["session_id"]),
            invocation_id=str(row["invocation_id"]),
            lease_token=str(row["lease_token"]),
            ip="",
            password=None,
        )
        return self._lease_manager.release_expired(
            lease,
            access_key=access_key,
            creator_id=creator_id,
        )

    def stop_unleased_ready_slot(
        self,
        row: dict[str, Any],
        *,
        access_key: str,
        creator_id: int = 0,
    ) -> HistoricalNodeStopOutcome:
        """Stop one audited ready slot after a fenced lease/state recheck."""
        identity = _identity_from_row(row)
        slot_id = int(row["id"])
        node_id = int(row["node_id"])
        with self._slot_lock(identity):
            current = self._nodes.find_by_id(slot_id)
            if (
                not current
                or current.get("state") != "ready"
                or current.get("node_id") is None
                or int(current["node_id"]) != node_id
            ):
                return HistoricalNodeStopOutcome.SKIPPED_SLOT_CHANGED
            self._leases.delete_expired_for_slot(slot_id)
            if self._leases.count_for_slot(slot_id) > 0:
                return HistoricalNodeStopOutcome.SKIPPED_CONCURRENT_LEASE
            if not self._nodes.mark_stopping(slot_id, node_id):
                return HistoricalNodeStopOutcome.SKIPPED_SLOT_CHANGED
        try:
            self._node_service.stop_node(
                access_key,
                node_id,
                identity.project_id,
                creator_id=creator_id,
            )
        except BohriumNodeNotFoundError:
            with self._slot_lock(identity):
                current = self._nodes.find_by_id(slot_id)
                if current is None:
                    return (
                        HistoricalNodeStopOutcome.PROVIDER_MISSING_SLOT_ALREADY_ABSENT
                    )
                if (
                    current.get("state") != "stopping"
                    or current.get("node_id") is None
                    or int(current["node_id"]) != node_id
                ):
                    return HistoricalNodeStopOutcome.SKIPPED_SLOT_CHANGED
                if self._nodes.delete_stopping_slot(slot_id, node_id):
                    return HistoricalNodeStopOutcome.PROVIDER_MISSING_SLOT_REMOVED
                return HistoricalNodeStopOutcome.SKIPPED_SLOT_CHANGED
        except Exception as exc:
            self._nodes.record_stop_error(slot_id, node_id, str(exc))
            raise
        with self._slot_lock(identity):
            if not self._nodes.mark_paused(slot_id, node_id):
                raise RuntimeError("Bohrium historical node stop state was fenced")
        return HistoricalNodeStopOutcome.STOPPED_TO_PAUSED

    def reconcile_stopped_unleased_ready_slot(
        self, row: dict[str, Any]
    ) -> HistoricalNodeStopOutcome:
        """Reconcile one provider-stopped historical ready slot to paused."""
        identity = _identity_from_row(row)
        slot_id = int(row["id"])
        node_id = int(row["node_id"])
        with self._slot_lock(identity):
            current = self._nodes.find_by_id(slot_id)
            if (
                not current
                or current.get("state") != "ready"
                or current.get("node_id") is None
                or int(current["node_id"]) != node_id
            ):
                return HistoricalNodeStopOutcome.SKIPPED_SLOT_CHANGED
            self._leases.delete_expired_for_slot(slot_id)
            if self._leases.count_for_slot(slot_id) > 0:
                return HistoricalNodeStopOutcome.SKIPPED_CONCURRENT_LEASE
            if not self._nodes.mark_ready_paused(slot_id, node_id):
                return HistoricalNodeStopOutcome.SKIPPED_SLOT_CHANGED
        return HistoricalNodeStopOutcome.ALREADY_STOPPED_TO_PAUSED

    def retry_stopping(
        self,
        row: dict[str, Any],
        *,
        access_key: str,
        creator_id: int = 0,
    ) -> bool:
        """Retry a previously failed provider stop without touching leases."""
        identity = _identity_from_row(row)
        slot_id = int(row["id"])
        node_id = int(row["node_id"])
        with self._slot_lock(identity):
            current = self._nodes.find_by_id(slot_id)
            if (
                not current
                or current.get("state") != "stopping"
                or current.get("node_id") is None
                or int(current["node_id"]) != node_id
            ):
                return False
            if self._has_leases_after_expired_cleanup(slot_id):
                return False
        detail = self._node_service.get_node_detail(access_key, node_id)
        if detail is None or detail.get("status") == NODE_STATUS_STOPPED:
            with self._slot_lock(identity):
                current = self._nodes.find_by_id(slot_id)
                if (
                    not current
                    or current.get("state") != "stopping"
                    or current.get("node_id") is None
                    or int(current["node_id"]) != node_id
                    or self._has_leases_after_expired_cleanup(slot_id)
                ):
                    return False
                if detail is None:
                    return self._nodes.delete_stopping_slot(slot_id, node_id)
                return self._nodes.mark_paused(slot_id, node_id)
        try:
            self._node_service.stop_node(
                access_key,
                node_id,
                identity.project_id,
                creator_id=creator_id,
            )
        except BohriumNodeNotFoundError:
            with self._slot_lock(identity):
                current = self._nodes.find_by_id(slot_id)
                if (
                    not current
                    or current.get("state") != "stopping"
                    or current.get("node_id") is None
                    or int(current["node_id"]) != node_id
                    or self._has_leases_after_expired_cleanup(slot_id)
                ):
                    return False
                return self._nodes.delete_stopping_slot(slot_id, node_id)
        except Exception as exc:
            self._nodes.record_stop_error(slot_id, node_id, str(exc))
            raise
        with self._slot_lock(identity):
            current = self._nodes.find_by_id(slot_id)
            if (
                not current
                or current.get("state") != "stopping"
                or current.get("node_id") is None
                or int(current["node_id"]) != node_id
                or self._has_leases_after_expired_cleanup(slot_id)
            ):
                return False
            return self._nodes.mark_paused(slot_id, node_id)

    def recycle_expired_creation(
        self,
        row: dict[str, Any],
        *,
        access_key: str,
        creator_id: int = 0,
    ) -> bool:
        """Fence an expired create/restart claim and stop its provider Node."""
        identity = _identity_from_row(row)
        slot_id = int(row["id"])
        token = str(row["creating_lease_token"])
        node_id = row.get("node_id")
        with self._slot_lock(identity):
            current = self._nodes.find_by_id(slot_id)
            if (
                not current
                or current.get("state") != "creating"
                or current.get("creating_lease_token") != token
                or self._has_leases_after_expired_cleanup(slot_id)
            ):
                return False
            if node_id is None:
                return self._nodes.delete_expired_empty_creation(slot_id, token)
            node_id = int(node_id)
            if not self._nodes.mark_stopping_expired_creation(slot_id, node_id, token):
                return False
        try:
            self._node_service.stop_node(
                access_key,
                node_id,
                identity.project_id,
                creator_id=creator_id,
            )
        except BohriumNodeNotFoundError:
            return self._nodes.delete_by_node(
                identity.user_id,
                identity.org_id,
                identity.project_id,
                identity.sku_id,
                node_id,
            )
        except Exception as exc:
            self._nodes.record_stop_error(slot_id, node_id, str(exc))
            raise
        with self._slot_lock(identity):
            return self._nodes.mark_paused(slot_id, node_id)


def _identity_from_row(row: dict[str, Any]) -> NodeIdentity:
    return NodeIdentity(
        str(row["user_id"]),
        str(row["org_id"]),
        int(row["project_id"]),
        int(row["sku_id"]),
    )


@lru_cache
def get_bohrium_node_reconciliation_service() -> BohriumNodeReconciliationService:
    return BohriumNodeReconciliationService()
