"""Distributed slot and invocation lease lifecycle for Bohrium Nodes."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Any

from src.dao.bohrium_node_leases_table import get_bohrium_node_leases_table
from src.dao.bohrium_nodes_table import get_bohrium_nodes_table
from src.dao.redis_dao import get_redis_dao
from src.services.bohrium_node_service import (
    BohriumNodeNotFoundError,
    get_bohrium_node_service,
)
from src.utils.constant import (
    BOHRIUM_DEFAULT_IMAGE_ID,
    BOHRIUM_DEFAULT_IMAGE_NAME,
    env_int,
)

logger = logging.getLogger(__name__)

_SLOT_LOCK_PREFIX = "matmaster:bohrium:node-slot:"


class NodeLifecyclePolicy(str, Enum):
    RUN_END = "run_end"
    IDLE_TIMEOUT = "idle_timeout"
    KEEP_RUNNING = "keep_running"


NODE_IDLE_TIMEOUT_OPTIONS_SECONDS = frozenset({900, 1800, 7200})


def resolve_node_lifecycle(
    policy: str | NodeLifecyclePolicy | None,
    idle_timeout_seconds: int | None,
) -> tuple[NodeLifecyclePolicy, int | None]:
    """Validate and normalize one per-invocation lifecycle snapshot."""
    try:
        resolved = NodeLifecyclePolicy(policy or NodeLifecyclePolicy.RUN_END)
    except ValueError as exc:
        raise ValueError(
            f"unsupported Bohrium Node lifecycle policy: {policy}"
        ) from exc
    if resolved is NodeLifecyclePolicy.IDLE_TIMEOUT:
        if idle_timeout_seconds not in NODE_IDLE_TIMEOUT_OPTIONS_SECONDS:
            raise ValueError("unsupported Bohrium Node idle timeout")
        return resolved, idle_timeout_seconds
    if idle_timeout_seconds is not None:
        raise ValueError("idle timeout is only valid for idle_timeout policy")
    return resolved, None


class HistoricalNodeStopOutcome(str, Enum):
    """Terminal outcomes for one explicitly audited historical slot."""

    STOPPED_TO_PAUSED = "STOPPED_TO_PAUSED"
    ALREADY_STOPPED_TO_PAUSED = "ALREADY_STOPPED_TO_PAUSED"
    SKIPPED_SLOT_CHANGED = "SKIPPED_SLOT_CHANGED"
    SKIPPED_CONCURRENT_LEASE = "SKIPPED_CONCURRENT_LEASE"
    PROVIDER_MISSING_SLOT_REMOVED = "PROVIDER_MISSING_SLOT_REMOVED"
    PROVIDER_MISSING_SLOT_ALREADY_ABSENT = "PROVIDER_MISSING_SLOT_ALREADY_ABSENT"


@dataclass(frozen=True)
class NodeIdentity:
    user_id: str
    org_id: str
    project_id: int
    sku_id: int

    @property
    def lock_key(self) -> str:
        raw = "\0".join(
            (
                self.user_id,
                self.org_id,
                str(self.project_id),
                str(self.sku_id),
            )
        )
        return f"{_SLOT_LOCK_PREFIX}{hashlib.sha256(raw.encode()).hexdigest()}"


@dataclass(frozen=True)
class NodeLease:
    identity: NodeIdentity
    node_slot_id: int
    node_id: int
    session_id: str
    invocation_id: str
    lease_token: str
    ip: str
    password: str | None


@dataclass(frozen=True)
class NodeLeaseConfig:
    lease_ttl_seconds: int = 120
    creation_ttl_seconds: int = 900
    slot_lock_ttl_seconds: int = 30
    acquire_timeout_seconds: int = 960
    retry_interval_seconds: float = 1.0

    @classmethod
    def from_env(cls) -> NodeLeaseConfig:
        return cls(
            lease_ttl_seconds=env_int("BOHRIUM_NODE_LEASE_TTL_SEC", 120),
            creation_ttl_seconds=env_int("BOHRIUM_NODE_CREATION_TTL_SEC", 900),
            slot_lock_ttl_seconds=env_int("BOHRIUM_NODE_SLOT_LOCK_TTL_SEC", 30),
            acquire_timeout_seconds=env_int("BOHRIUM_NODE_ACQUIRE_TIMEOUT_SEC", 960),
            retry_interval_seconds=float(
                env_int("BOHRIUM_NODE_ACQUIRE_RETRY_INTERVAL_SEC", 1)
            ),
        )


class NodeLeaseHeartbeat:
    """Run-owned daemon that renews one fenced invocation lease."""

    def __init__(
        self,
        manager: BohriumNodeLeaseManager,
        lease: NodeLease,
        *,
        interval_seconds: float = 30.0,
    ) -> None:
        self._manager = manager
        self._lease = lease
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name=f"bohrium-node-lease-{self._lease.invocation_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self._interval_seconds + 1.0))

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                if not self._manager.heartbeat(self._lease):
                    logger.warning(
                        "Bohrium node lease heartbeat fenced invocation_id=%s",
                        self._lease.invocation_id,
                    )
                    return
            except Exception:
                logger.warning(
                    "Bohrium node lease heartbeat failed invocation_id=%s",
                    self._lease.invocation_id,
                    exc_info=True,
                )


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

    @contextmanager
    def _slot_lock(self, identity: NodeIdentity) -> Iterator[None]:
        token = str(uuid.uuid4())
        deadline = time.monotonic() + self._config.acquire_timeout_seconds
        while True:
            reserved = self._redis.try_reserve_nx(
                identity.lock_key,
                token,
                self._config.slot_lock_ttl_seconds,
            )
            if reserved is None:
                raise RuntimeError(
                    "Redis unavailable while acquiring Bohrium node slot"
                )
            if reserved:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out acquiring Bohrium node slot lock")
            time.sleep(self._config.retry_interval_seconds)
        try:
            yield
        finally:
            self._redis.release_reservation(identity.lock_key, token)

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
    ) -> NodeLease:
        """Acquire an invocation lease, creating or restarting the shared Node."""
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
        while time.monotonic() < deadline:
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
                )
            time.sleep(self._config.retry_interval_seconds)
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
    ) -> NodeLease:
        created_new = action in {"create", "replace"}
        try:
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
            info = self._node_service.wait_until_ready(access_key, node_id)
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
        """Fence a heartbeat racing an expired-lease cleanup before stop claims."""
        self._leases.delete_expired_for_slot(slot_id)
        return self._leases.count_for_slot(slot_id) > 0

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
        """Stop a due idle_timeout candidate after a locked CAS recheck."""
        identity = NodeIdentity(
            str(row["user_id"]),
            str(row["org_id"]),
            int(row["project_id"]),
            int(row["sku_id"]),
        )
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
            identity=NodeIdentity(
                str(row["user_id"]),
                str(row["org_id"]),
                int(row["project_id"]),
                int(row["sku_id"]),
            ),
            node_slot_id=int(row["node_slot_id"]),
            node_id=int(row["node_id"]),
            session_id=str(row["session_id"]),
            invocation_id=str(row["invocation_id"]),
            lease_token=str(row["lease_token"]),
            ip="",
            password=None,
        )
        return self.release_expired(lease, access_key=access_key, creator_id=creator_id)

    def stop_unleased_ready_slot(
        self,
        row: dict[str, Any],
        *,
        access_key: str,
        creator_id: int = 0,
    ) -> HistoricalNodeStopOutcome:
        """Stop one audited ready slot after a fenced lease/state recheck."""
        identity = NodeIdentity(
            str(row["user_id"]),
            str(row["org_id"]),
            int(row["project_id"]),
            int(row["sku_id"]),
        )
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
        identity = NodeIdentity(
            str(row["user_id"]),
            str(row["org_id"]),
            int(row["project_id"]),
            int(row["sku_id"]),
        )
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
        identity = NodeIdentity(
            str(row["user_id"]),
            str(row["org_id"]),
            int(row["project_id"]),
            int(row["sku_id"]),
        )
        slot_id = int(row["id"])
        node_id = int(row["node_id"])
        with self._slot_lock(identity):
            current = self._nodes.find_by_id(slot_id)
            if not current or current.get("state") != "stopping":
                return False
            if self._has_leases_after_expired_cleanup(slot_id):
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

    def recycle_expired_creation(
        self,
        row: dict[str, Any],
        *,
        access_key: str,
        creator_id: int = 0,
    ) -> bool:
        """Fence an expired create/restart claim and stop its provider Node."""
        identity = NodeIdentity(
            str(row["user_id"]),
            str(row["org_id"]),
            int(row["project_id"]),
            int(row["sku_id"]),
        )
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


@lru_cache
def get_bohrium_node_lease_manager() -> BohriumNodeLeaseManager:
    return BohriumNodeLeaseManager()
