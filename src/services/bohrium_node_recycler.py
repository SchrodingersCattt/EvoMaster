"""Monitor-side orphan recycler for expired Bohrium Node invocation leases."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.services.bohrium_run_support import _creator_id_from_user
from src.utils.constant import env_int

logger = logging.getLogger(__name__)

_LOCK_KEY = "matmaster:monitor:bohrium_node_recycler:lock"


@dataclass(frozen=True)
class BohriumNodeRecyclerConfig:
    enabled: bool = True
    batch_size: int = 100
    lock_ttl_seconds: int = 90
    stop_retry_min_age_seconds: int = 120

    @classmethod
    def from_env(cls) -> BohriumNodeRecyclerConfig:
        enabled = os.getenv("BOHRIUM_NODE_RECYCLER_ENABLED", "true").strip()
        return cls(
            enabled=enabled.lower() in {"1", "true", "yes", "on"},
            batch_size=env_int("BOHRIUM_NODE_RECYCLER_BATCH_SIZE", 100),
            lock_ttl_seconds=env_int("BOHRIUM_NODE_RECYCLER_LOCK_TTL_SEC", 90),
            stop_retry_min_age_seconds=env_int(
                "BOHRIUM_NODE_STOP_RETRY_MIN_AGE_SEC", 120
            ),
        )


class BohriumNodeRecycler:
    """One monitor tick; all exceptions are counted and contained."""

    def __init__(
        self,
        *,
        redis: Any | None = None,
        leases_table: Any | None = None,
        nodes_table: Any | None = None,
        lease_manager: Any | None = None,
        access_key_loader: Callable[[str, str], str | None] | None = None,
        config: BohriumNodeRecyclerConfig | None = None,
    ) -> None:
        self._redis = redis
        self._leases = leases_table
        self._nodes = nodes_table
        self._manager = lease_manager
        self._access_key_loader = access_key_loader
        self._config = config or BohriumNodeRecyclerConfig.from_env()

    def _ensure_deps(self) -> None:
        if self._redis is None:
            from src.dao.redis_dao import get_redis_dao

            self._redis = get_redis_dao()
        if self._leases is None:
            from src.dao.bohrium_node_leases_table import (
                get_bohrium_node_leases_table,
            )

            self._leases = get_bohrium_node_leases_table()
        if self._nodes is None:
            from src.dao.bohrium_nodes_table import get_bohrium_nodes_table

            self._nodes = get_bohrium_nodes_table()
        if self._manager is None:
            from src.services.bohrium_node_lifecycle import (
                get_bohrium_node_lease_manager,
            )

            self._manager = get_bohrium_node_lease_manager()
        if self._access_key_loader is None:
            from src.services.user_service import UserService

            self._access_key_loader = UserService.get_bohrium_access_key

    def tick(self) -> dict[str, int]:
        summary = {
            "enabled": int(self._config.enabled),
            "creating_scanned": 0,
            "creating_recycled": 0,
            "stopping_scanned": 0,
            "stop_retried": 0,
            "idle_scanned": 0,
            "idle_stopped": 0,
            "expired_scanned": 0,
            "expired_released": 0,
            "skipped_credentials": 0,
            "skipped_lock": 0,
            "skipped_redis": 0,
            "errors": 0,
            "tick_failed": 0,
        }
        if not self._config.enabled:
            return summary
        lock_token = str(uuid.uuid4())
        locked = False
        try:
            self._ensure_deps()
            reserved = self._redis.try_reserve_nx(
                _LOCK_KEY, lock_token, self._config.lock_ttl_seconds
            )
            if reserved is None:
                summary["skipped_redis"] = 1
                return summary
            if reserved is False:
                summary["skipped_lock"] = 1
                return summary
            locked = True
            self._recycle_creating(summary)
            self._retry_stopping(summary)
            self._release_expired(summary)
            self._stop_due_idle(summary)
            return summary
        except Exception:
            summary["tick_failed"] = 1
            logger.warning("Bohrium node recycler tick failed", exc_info=True)
            return summary
        finally:
            if locked:
                self._redis.release_reservation(_LOCK_KEY, lock_token)

    def _recycle_creating(self, summary: dict[str, int]) -> None:
        rows = self._nodes.list_expired_creating_slots(self._config.batch_size)
        summary["creating_scanned"] = len(rows)
        for row in rows:
            try:
                access_key = ""
                if row.get("node_id") is not None:
                    access_key = self._load_access_key(row, summary) or ""
                    if not access_key:
                        continue
                if self._manager.recycle_expired_creation(
                    row,
                    access_key=access_key,
                    creator_id=_creator_id_from_user(row.get("user_id")),
                ):
                    summary["creating_recycled"] += 1
            except Exception:
                summary["errors"] += 1
                logger.warning(
                    "Bohrium expired node creation recycle failed slot_id=%s",
                    row.get("id"),
                    exc_info=True,
                )

    def _load_access_key(
        self, row: dict[str, Any], summary: dict[str, int]
    ) -> str | None:
        access_key = self._access_key_loader(
            str(row.get("user_id") or ""), str(row.get("org_id") or "")
        )
        if not access_key:
            summary["skipped_credentials"] += 1
        return access_key

    def _retry_stopping(self, summary: dict[str, int]) -> None:
        rows = self._nodes.list_stopping_without_live_leases(
            self._config.batch_size,
            self._config.stop_retry_min_age_seconds,
        )
        summary["stopping_scanned"] = len(rows)
        for row in rows:
            try:
                access_key = self._load_access_key(row, summary)
                if not access_key:
                    continue
                if self._manager.retry_stopping(
                    row,
                    access_key=access_key,
                    creator_id=_creator_id_from_user(row.get("user_id")),
                ):
                    summary["stop_retried"] += 1
            except Exception:
                summary["errors"] += 1
                logger.warning(
                    "Bohrium node stopping retry failed slot_id=%s",
                    row.get("id"),
                    exc_info=True,
                )

    def _release_expired(self, summary: dict[str, int]) -> None:
        rows = self._leases.list_expired(self._config.batch_size)
        summary["expired_scanned"] = len(rows)
        for lease_row in rows:
            try:
                slot = self._nodes.find_by_id(int(lease_row["node_slot_id"]))
                if not slot or slot.get("node_id") is None:
                    if self._leases.release_expired(
                        str(lease_row["invocation_id"]),
                        str(lease_row["lease_token"]),
                    ):
                        summary["expired_released"] += 1
                    continue
                access_key = self._load_access_key(slot, summary)
                if not access_key:
                    continue
                row = {**slot, **lease_row}
                outcome = self._manager.release_expired_row(
                    row,
                    access_key=access_key,
                    creator_id=_creator_id_from_user(slot.get("user_id")),
                )
                if outcome is not None:
                    summary["expired_released"] += 1
            except Exception:
                summary["errors"] += 1
                logger.warning(
                    "Bohrium expired node lease recycle failed invocation_id=%s",
                    lease_row.get("invocation_id"),
                    exc_info=True,
                )

    def _stop_due_idle(self, summary: dict[str, int]) -> None:
        rows = self._nodes.list_due_idle_slots(self._config.batch_size)
        summary["idle_scanned"] = len(rows)
        for row in rows:
            try:
                access_key = self._load_access_key(row, summary)
                if not access_key:
                    continue
                if self._manager.stop_due_idle(
                    row,
                    access_key=access_key,
                    creator_id=_creator_id_from_user(row.get("user_id")),
                ):
                    summary["idle_stopped"] += 1
            except Exception:
                summary["errors"] += 1
                logger.warning(
                    "Bohrium due idle node recycle failed slot_id=%s",
                    row.get("id"),
                    exc_info=True,
                )
