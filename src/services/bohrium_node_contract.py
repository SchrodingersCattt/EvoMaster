"""Shared contracts for Bohrium Node leases and lifecycle policies."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

from matmaster.bohrium.node_lifecycle import (
    NODE_IDLE_TIMEOUT_OPTIONS_SECONDS,
    NodeLifecyclePolicy,
    resolve_node_lifecycle,
)
from src.utils.constant import env_int

_SLOT_LOCK_PREFIX = "matmaster:bohrium:node-slot:"

__all__ = [
    "HistoricalNodeStopOutcome",
    "NODE_IDLE_TIMEOUT_OPTIONS_SECONDS",
    "NodeIdentity",
    "NodeLease",
    "NodeLeaseConfig",
    "NodeLifecyclePolicy",
    "resolve_node_lifecycle",
]


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
