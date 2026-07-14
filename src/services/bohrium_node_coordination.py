"""Shared Redis-lock and lease-fencing primitives for Bohrium Node services."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from src.services.bohrium_node_contract import NodeIdentity, NodeLeaseConfig


@contextmanager
def node_slot_lock(
    redis: Any,
    identity: NodeIdentity,
    config: NodeLeaseConfig,
) -> Iterator[None]:
    """Acquire the short distributed lock guarding one reusable Node slot."""
    token = str(uuid.uuid4())
    deadline = time.monotonic() + config.acquire_timeout_seconds
    while True:
        reserved = redis.try_reserve_nx(
            identity.lock_key,
            token,
            config.slot_lock_ttl_seconds,
        )
        if reserved is None:
            raise RuntimeError("Redis unavailable while acquiring Bohrium node slot")
        if reserved:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out acquiring Bohrium node slot lock")
        time.sleep(config.retry_interval_seconds)
    try:
        yield
    finally:
        redis.release_reservation(identity.lock_key, token)


def has_leases_after_expired_cleanup(leases: Any, slot_id: int) -> bool:
    """Fence a heartbeat racing an expired-lease cleanup before stop claims."""
    leases.delete_expired_for_slot(slot_id)
    return leases.count_for_slot(slot_id) > 0
