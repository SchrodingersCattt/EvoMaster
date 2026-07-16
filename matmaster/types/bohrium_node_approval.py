"""Contracts for approving a lazy Bohrium Node start."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

BohriumNodeStartOutcome = Literal[
    "approved",
    "rejected",
    "busy",
    "timeout",
    "cancelled",
    "invalid",
]


@dataclass(frozen=True)
class BohriumNodeStartRequest:
    """Snapshot presented when a tool first requires a Bohrium Node."""

    request_id: str
    trigger_reason: str
    default_lifecycle_policy: str
    default_idle_timeout_seconds: int | None = None
    node_sku_id: int | None = None
    timeout_seconds: int | None = None


@dataclass(frozen=True)
class BohriumNodeStartDecision:
    """Approval result, including the lifecycle selected for this run."""

    review_outcome: BohriumNodeStartOutcome
    lifecycle_policy: str | None = None
    idle_timeout_seconds: int | None = None

    @property
    def approved(self) -> bool:
        return self.review_outcome == "approved"


@runtime_checkable
class BohriumNodeStartApprovalGate(Protocol):
    """Interactive service port consumed once, immediately before Node creation.

    ``review`` returns a normalized lifecycle selection on approval. Expected
    user rejection, timeout, cancellation, busy state, and malformed replies
    are represented as decisions; transport failures may raise.
    """

    async def review(
        self, request: BohriumNodeStartRequest
    ) -> BohriumNodeStartDecision: ...


__all__ = [
    "BohriumNodeStartApprovalGate",
    "BohriumNodeStartDecision",
    "BohriumNodeStartOutcome",
    "BohriumNodeStartRequest",
]
