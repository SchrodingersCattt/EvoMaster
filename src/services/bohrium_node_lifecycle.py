"""Distributed slot and invocation lease lifecycle for Bohrium Nodes."""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Callable
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
    NodeLifecyclePolicy,
    resolve_node_lifecycle,
)
from src.services.bohrium_node_coordination import (
    has_leases_after_expired_cleanup,
    node_slot_lock,
)
from src.services.bohrium_node_progress import (
    NodeProgressReporter,
    report_node_progress,
)
from src.services.bohrium_node_service import (
    BohriumNodeNotFoundError,
    get_bohrium_node_service,
)
from src.utils.constant import BOHRIUM_DEFAULT_IMAGE_ID, BOHRIUM_DEFAULT_IMAGE_NAME

logger = logging.getLogger(__name__)


def _raise_if_cancelled(cancel_checker: Callable[[], bool] | None) -> None:
    if cancel_checker is not None and cancel_checker():
        raise RuntimeError("Bohrium Node acquisition cancelled")


def _sleep_or_cancel(seconds: float, cancel_checker: Callable[[], bool] | None) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _raise_if_cancelled(cancel_checker)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.1, remaining))


class BohriumNodeLeaseManager:
    """Coordinates short slot transitions; provider waits happen outside locks."""

    def __init__(
        self,
        *,
        nodes_table: Any | None = None,
        leases_table: Any | None = None,
        redis: Any | None = None,
        node_service: Any | None = None,
        config: NodeLeaseConfig | None = None,
    ) -> None:
        self._nodes = nodes_table or get_bohrium_nodes_table()
        self._leases = leases_table or get_bohrium_node_leases_table()
        self._redis = redis or get_redis_dao()
        self._node_service = node_service or get_bohrium_node_service()
        self._config = config or NodeLeaseConfig.from_env()

    def _slot_lock(self, identity: NodeIdentity):
        return node_slot_lock(self._redis, identity, self._config)

    def acquire(
        self,
        identity: NodeIdentity,
        *,
        session_id: str,
        invocation_id: str,
        access_key: str,
        creator_id: int = 0,
        expected_image_name: str | None = None,
        lifecycle_policy: str | NodeLifecyclePolicy | None = None,
        idle_timeout_seconds: int | None = None,
        progress_reporter: NodeProgressReporter | None = None,
        cancel_checker: Callable[[], bool] | None = None,
    ) -> NodeLease:
        """Acquire an invocation lease, creating or restarting the shared Node."""
        _raise_if_cancelled(cancel_checker)
        policy, idle_timeout_seconds = resolve_node_lifecycle(
            lifecycle_policy, idle_timeout_seconds
        )
        if expected_image_name is None:
            expected_image_name = (
                os.environ.get("BOHRIUM_EXPECTED_IMAGE_NAME")
                or os.environ.get("BOHRIUM_IMAGE_NAME")
                or BOHRIUM_DEFAULT_IMAGE_NAME
            )
            expected_image_name = expected_image_name.strip() or None
            if expected_image_name is None:
                expected_image_name = self._node_service.get_image_name_by_id(
                    access_key, BOHRIUM_DEFAULT_IMAGE_ID
                )
        lease_token = str(uuid.uuid4())
        creation_token = str(uuid.uuid4())
        deadline = time.monotonic() + self._config.acquire_timeout_seconds
        reported_waiting = False
        while time.monotonic() < deadline:
            _raise_if_cancelled(cancel_checker)
            action: str | None = None
            row: dict[str, Any] | None = None
            with self._slot_lock(identity):
                row = self._nodes.find_one_for_reuse(
                    identity.user_id,
                    identity.org_id,
                    identity.project_id,
                    identity.sku_id,
                )
                if row is None:
                    self._nodes.insert_creating_slot(
                        identity.user_id,
                        identity.org_id,
                        identity.project_id,
                        identity.sku_id,
                        invocation_id,
                        creation_token,
                        self._config.creation_ttl_seconds,
                    )
                    row = self._nodes.find_one_for_reuse(
                        identity.user_id,
                        identity.org_id,
                        identity.project_id,
                        identity.sku_id,
                    )
                if row is None:
                    continue
                state = str(row.get("state") or "ready")
                node_id = row.get("node_id")
                if state == "ready" and node_id is not None:
                    if not self._nodes.set_lifecycle_policy(
                        int(row["id"]), policy.value, idle_timeout_seconds
                    ):
                        continue
                    self._leases.acquire(
                        int(row["id"]),
                        session_id,
                        invocation_id,
                        lease_token,
                        self._config.lease_ttl_seconds,
                    )
                    action = "reuse"
                elif state == "idle" and node_id is not None:
                    if self._nodes.claim_idle_for_acquire(
                        int(row["id"]),
                        int(node_id),
                        policy.value,
                        idle_timeout_seconds,
                    ):
                        self._leases.acquire(
                            int(row["id"]),
                            session_id,
                            invocation_id,
                            lease_token,
                            self._config.lease_ttl_seconds,
                        )
                        row = {
                            **row,
                            "state": "ready",
                            "lifecycle_policy": policy.value,
                            "idle_timeout_seconds": idle_timeout_seconds,
                            "idle_expires_at": None,
                        }
                        action = "reuse"
                elif state == "paused" and node_id is not None:
                    if self._nodes.begin_restart(
                        int(row["id"]),
                        int(node_id),
                        invocation_id,
                        creation_token,
                        self._config.creation_ttl_seconds,
                    ):
                        self._nodes.set_lifecycle_policy(
                            int(row["id"]), policy.value, idle_timeout_seconds
                        )
                        action = "restart"
                elif state == "creating":
                    if row.get("creating_lease_token") == creation_token:
                        self._nodes.set_lifecycle_policy(
                            int(row["id"]), policy.value, idle_timeout_seconds
                        )
                        action = "create" if node_id is None else "restart"
                    elif self._nodes.claim_expired_creation(
                        int(row["id"]),
                        invocation_id,
                        creation_token,
                        self._config.creation_ttl_seconds,
                    ):
                        self._nodes.set_lifecycle_policy(
                            int(row["id"]), policy.value, idle_timeout_seconds
                        )
                        action = "create" if node_id is None else "restart"
                    else:
                        action = "wait"

            if action == "wait" and row is not None:
                if not reported_waiting:
                    waiting_node_id = row.get("node_id")
                    report_node_progress(
                        progress_reporter,
                        "waiting",
                        int(waiting_node_id) if waiting_node_id is not None else None,
                        "共享节点正在启动，等待就绪...",
                    )
                    reported_waiting = True
                _sleep_or_cancel(self._config.retry_interval_seconds, cancel_checker)
                continue

            if action == "reuse" and row is not None:
                node_id = int(row["node_id"])
                info = self._node_service.get_node_info(access_key, node_id)
                image_outdated = bool(
                    expected_image_name
                    and info
                    and info.get("image_name")
                    and info.get("image_name") != expected_image_name
                )
                if info and info.get("ip") and not image_outdated:
                    return self._handle(
                        identity,
                        row,
                        info,
                        session_id,
                        invocation_id,
                        lease_token,
                    )
                with self._slot_lock(identity):
                    can_prepare = not image_outdated or (
                        self._leases.count_live(int(row["id"])) == 1
                    )
                    if can_prepare and self._nodes.begin_restart(
                        int(row["id"]),
                        node_id,
                        invocation_id,
                        creation_token,
                        self._config.creation_ttl_seconds,
                    ):
                        action = "replace" if image_outdated else "restart"
                    else:
                        action = None

                if image_outdated and action is None and info and info.get("ip"):
                    logger.warning(
                        "Bohrium node image replacement deferred for active leases "
                        "node_id=%s expected=%s actual=%s",
                        node_id,
                        expected_image_name,
                        info.get("image_name"),
                    )
                    return self._handle(
                        identity,
                        row,
                        info,
                        session_id,
                        invocation_id,
                        lease_token,
                    )

            if action in {"create", "restart", "replace"} and row is not None:
                preparing_node_id = row.get("node_id")
                progress_status = "restarting" if action == "restart" else "creating"
                report_node_progress(
                    progress_reporter,
                    progress_status,
                    int(preparing_node_id) if preparing_node_id is not None else None,
                    (
                        "正在重启 Bohrium 计算节点..."
                        if progress_status == "restarting"
                        else "正在创建 Bohrium 计算节点..."
                    ),
                )
                return self._prepare_and_publish(
                    action,
                    identity,
                    row,
                    session_id=session_id,
                    invocation_id=invocation_id,
                    lease_token=lease_token,
                    creation_token=creation_token,
                    access_key=access_key,
                    creator_id=creator_id,
                    expected_image_name=expected_image_name,
                    progress_reporter=progress_reporter,
                    cancel_checker=cancel_checker,
                )
            _sleep_or_cancel(self._config.retry_interval_seconds, cancel_checker)
        raise TimeoutError("Timed out waiting for shared Bohrium node slot")

    def _prepare_and_publish(
        self,
        action: str,
        identity: NodeIdentity,
        row: dict[str, Any],
        *,
        session_id: str,
        invocation_id: str,
        lease_token: str,
        creation_token: str,
        access_key: str,
        creator_id: int,
        expected_image_name: str | None,
        progress_reporter: NodeProgressReporter | None,
        cancel_checker: Callable[[], bool] | None,
    ) -> NodeLease:
        created_new = action in {"create", "replace"}
        try:
            _raise_if_cancelled(cancel_checker)
            if action == "restart" and expected_image_name:
                detail = self._node_service.get_node_detail(
                    access_key, int(row["node_id"])
                )
                if (
                    detail
                    and detail.get("image_name")
                    and detail.get("image_name") != expected_image_name
                ):
                    action = "replace"
                    created_new = True
            if action == "restart":
                node_id = int(row["node_id"])
                try:
                    self._node_service.restart_node(
                        access_key,
                        node_id,
                        identity.project_id,
                        creator_id=creator_id,
                        sku_id=identity.sku_id,
                    )
                except Exception:
                    logger.warning(
                        "Bohrium node restart failed; replacing node_id=%s",
                        node_id,
                        exc_info=True,
                    )
                    self._node_service.destroy_node(
                        access_key,
                        node_id,
                        identity.project_id,
                        creator_id=creator_id,
                    )
                    created_new = True
            elif action == "replace":
                self._node_service.destroy_node(
                    access_key,
                    int(row["node_id"]),
                    identity.project_id,
                    creator_id=creator_id,
                )
            if created_new:
                _raise_if_cancelled(cancel_checker)
                created = self._node_service.create_node(
                    access_key,
                    identity.project_id,
                    sku_id=identity.sku_id,
                )
                node_id = int(created["node_id"])
                with self._slot_lock(identity):
                    attached = self._nodes.attach_creating_node(
                        int(row["id"]), creation_token, node_id
                    )
                if not attached:
                    self._node_service.destroy_node(
                        access_key,
                        node_id,
                        identity.project_id,
                        creator_id=creator_id,
                    )
                    raise RuntimeError("Bohrium node creation claim was fenced")
            report_node_progress(
                progress_reporter,
                "starting",
                node_id,
                (
                    "节点已创建，正在等待资源就绪..."
                    if created_new
                    else "节点已重启，正在等待资源就绪..."
                ),
            )
            wait_kwargs = (
                {'cancel_checker': cancel_checker} if cancel_checker is not None else {}
            )
            info = self._node_service.wait_until_ready(
                access_key, node_id, **wait_kwargs
            )
            _raise_if_cancelled(cancel_checker)
            with self._slot_lock(identity):
                self._leases.acquire(
                    int(row["id"]),
                    session_id,
                    invocation_id,
                    lease_token,
                    self._config.lease_ttl_seconds,
                )
                published = self._nodes.mark_ready(
                    int(row["id"]), creation_token, node_id
                )
                if not published:
                    self._leases.release(invocation_id, lease_token)
            if not published:
                if created_new:
                    self._node_service.destroy_node(
                        access_key,
                        node_id,
                        identity.project_id,
                        creator_id=creator_id,
                    )
                raise RuntimeError("Bohrium node creation claim was fenced")
            return self._handle(
                identity,
                row,
                info,
                session_id,
                invocation_id,
                lease_token,
            )
        except Exception as exc:
            self._nodes.expire_creation(int(row["id"]), creation_token, str(exc))
            raise

    @staticmethod
    def _handle(
        identity: NodeIdentity,
        row: dict[str, Any],
        info: dict[str, Any],
        session_id: str,
        invocation_id: str,
        lease_token: str,
    ) -> NodeLease:
        return NodeLease(
            identity=identity,
            node_slot_id=int(row["id"]),
            node_id=int(info["node_id"]),
            session_id=session_id,
            invocation_id=invocation_id,
            lease_token=lease_token,
            ip=str(info["ip"]),
            password=info.get("password"),
        )

    def heartbeat(self, lease: NodeLease) -> bool:
        return self._leases.heartbeat(
            lease.invocation_id,
            lease.lease_token,
            self._config.lease_ttl_seconds,
        )

    def _has_leases_after_expired_cleanup(self, slot_id: int) -> bool:
        return has_leases_after_expired_cleanup(self._leases, slot_id)

    def release(
        self,
        lease: NodeLease,
        *,
        access_key: str,
        creator_id: int = 0,
    ) -> bool:
        """Release own lease; only the last live lease transitions Node to paused."""
        return bool(
            self._release(
                lease,
                access_key=access_key,
                creator_id=creator_id,
                expired_only=False,
            )
        )

    def release_expired(
        self,
        lease: NodeLease,
        *,
        access_key: str,
        creator_id: int = 0,
    ) -> bool | None:
        """Release only if the scanned lease is still expired.

        None means a heartbeat/token change fenced the recycler; False means the
        lease was removed but another live lease kept the Node running.
        """
        return self._release(
            lease,
            access_key=access_key,
            creator_id=creator_id,
            expired_only=True,
        )

    def _release(
        self,
        lease: NodeLease,
        *,
        access_key: str,
        creator_id: int,
        expired_only: bool,
    ) -> bool | None:
        identity = lease.identity
        with self._slot_lock(identity):
            release_fn = (
                self._leases.release_expired if expired_only else self._leases.release
            )
            if not release_fn(lease.invocation_id, lease.lease_token):
                return None if expired_only else False
            if self._has_leases_after_expired_cleanup(lease.node_slot_id):
                self._nodes.update_last_used_at(
                    identity.user_id,
                    identity.org_id,
                    identity.project_id,
                    identity.sku_id,
                    lease.node_id,
                )
                return False
            current = self._nodes.find_by_id(lease.node_slot_id)
            policy, idle_timeout_seconds = resolve_node_lifecycle(
                current.get("lifecycle_policy") if current else None,
                current.get("idle_timeout_seconds") if current else None,
            )
            if policy in {
                NodeLifecyclePolicy.IDLE_TIMEOUT,
                NodeLifecyclePolicy.KEEP_RUNNING,
            }:
                if not self._nodes.mark_idle(
                    lease.node_slot_id,
                    lease.node_id,
                    policy.value,
                    idle_timeout_seconds,
                ):
                    return False
                return False
            if not self._nodes.mark_stopping(lease.node_slot_id, lease.node_id):
                return False
        try:
            self._node_service.stop_node(
                access_key,
                lease.node_id,
                identity.project_id,
                creator_id=creator_id,
            )
        except BohriumNodeNotFoundError:
            self._nodes.delete_by_node(
                identity.user_id,
                identity.org_id,
                identity.project_id,
                identity.sku_id,
                lease.node_id,
            )
            return True
        except Exception as exc:
            self._nodes.record_stop_error(lease.node_slot_id, lease.node_id, str(exc))
            raise
        with self._slot_lock(identity):
            if not self._nodes.mark_paused(lease.node_slot_id, lease.node_id):
                raise RuntimeError("Bohrium node stop state was fenced")
        return True

    def stop_due_idle(
        self,
        row: dict[str, Any],
        *,
        access_key: str,
        creator_id: int = 0,
    ) -> bool:
        """Compatibility facade; monitor code uses the reconciler directly."""
        return self._reconciliation_service().stop_due_idle(
            row,
            access_key=access_key,
            creator_id=creator_id,
        )

    def release_expired_row(
        self,
        row: dict[str, Any],
        *,
        access_key: str,
        creator_id: int = 0,
    ) -> bool | None:
        """Compatibility facade; monitor code uses the reconciler directly."""
        return self._reconciliation_service().release_expired_row(
            row,
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
        """Compatibility facade; audit code uses the reconciler directly."""
        return self._reconciliation_service().stop_unleased_ready_slot(
            row,
            access_key=access_key,
            creator_id=creator_id,
        )

    def reconcile_stopped_unleased_ready_slot(
        self, row: dict[str, Any]
    ) -> HistoricalNodeStopOutcome:
        """Compatibility facade; audit code uses the reconciler directly."""
        return self._reconciliation_service().reconcile_stopped_unleased_ready_slot(row)

    def retry_stopping(
        self,
        row: dict[str, Any],
        *,
        access_key: str,
        creator_id: int = 0,
    ) -> bool:
        """Compatibility facade; monitor code uses the reconciler directly."""
        return self._reconciliation_service().retry_stopping(
            row,
            access_key=access_key,
            creator_id=creator_id,
        )

    def recycle_expired_creation(
        self,
        row: dict[str, Any],
        *,
        access_key: str,
        creator_id: int = 0,
    ) -> bool:
        """Compatibility facade; monitor code uses the reconciler directly."""
        return self._reconciliation_service().recycle_expired_creation(
            row,
            access_key=access_key,
            creator_id=creator_id,
        )

    def _reconciliation_service(self):
        from src.services.bohrium_node_reconciliation import (
            BohriumNodeReconciliationService,
        )

        return BohriumNodeReconciliationService(
            nodes_table=self._nodes,
            leases_table=self._leases,
            redis=self._redis,
            node_service=self._node_service,
            lease_manager=self,
            config=self._config,
        )


@lru_cache
def get_bohrium_node_lease_manager() -> BohriumNodeLeaseManager:
    return BohriumNodeLeaseManager()
