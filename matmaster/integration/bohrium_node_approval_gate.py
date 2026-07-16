"""InteractionBridge adapter for just-in-time Bohrium Node approval."""

from __future__ import annotations

import asyncio
from typing import Any

from matmaster.bohrium.node_lifecycle import resolve_node_lifecycle
from matmaster.integration.interaction_bridge import (
    InteractionBridge,
    InteractionBusyError,
)
from matmaster.types import InteractionTimeoutEvent
from matmaster.types.bohrium_node_approval import (
    BohriumNodeStartDecision,
    BohriumNodeStartRequest,
)

BOHRIUM_NODE_START_REVIEW_KIND = "bohrium_node_start_review"
BOHRIUM_NODE_START_REVIEW_SCHEMA_VERSION = 1


def _request_to_payload(request: BohriumNodeStartRequest) -> dict[str, Any]:
    return {
        "schema_version": BOHRIUM_NODE_START_REVIEW_SCHEMA_VERSION,
        "trigger_reason": request.trigger_reason,
        "default_lifecycle_policy": request.default_lifecycle_policy,
        "default_idle_timeout_seconds": request.default_idle_timeout_seconds,
        "node_sku_id": request.node_sku_id,
    }


def _reply_to_decision(
    reply: dict[str, Any], request: BohriumNodeStartRequest
) -> BohriumNodeStartDecision:
    decision = reply.get("decision")
    if decision == "reject":
        return BohriumNodeStartDecision(review_outcome="rejected")
    if decision != "start":
        return BohriumNodeStartDecision(review_outcome="invalid")

    try:
        policy, idle_timeout = resolve_node_lifecycle(
            reply.get("lifecycle_policy", request.default_lifecycle_policy),
            reply.get("idle_timeout_seconds", request.default_idle_timeout_seconds),
        )
    except (TypeError, ValueError):
        return BohriumNodeStartDecision(review_outcome="invalid")
    return BohriumNodeStartDecision(
        review_outcome="approved",
        lifecycle_policy=policy.value,
        idle_timeout_seconds=idle_timeout,
    )


class BridgeBohriumNodeStartApprovalGate:
    """Wait for the frontend immediately before the first Node acquisition."""

    def __init__(self, bridge: InteractionBridge) -> None:
        self._bridge = bridge

    async def review(
        self, request: BohriumNodeStartRequest
    ) -> BohriumNodeStartDecision:
        try:
            reply = await self._bridge.request(
                kind=BOHRIUM_NODE_START_REVIEW_KIND,
                request_id=request.request_id,
                payload=_request_to_payload(request),
                timeout_seconds=request.timeout_seconds,
            )
        except InteractionBusyError:
            return BohriumNodeStartDecision(review_outcome="busy")
        except TimeoutError:
            await self._emit_timeout(request.request_id)
            return BohriumNodeStartDecision(review_outcome="timeout")
        except asyncio.CancelledError:
            return BohriumNodeStartDecision(review_outcome="cancelled")
        return _reply_to_decision(reply, request)

    async def _emit_timeout(self, request_id: str) -> None:
        await self._bridge.emit(
            InteractionTimeoutEvent(
                source="System",
                kind=BOHRIUM_NODE_START_REVIEW_KIND,
                request_id=request_id,
            )
        )


__all__ = [
    "BOHRIUM_NODE_START_REVIEW_KIND",
    "BOHRIUM_NODE_START_REVIEW_SCHEMA_VERSION",
    "BridgeBohriumNodeStartApprovalGate",
]
