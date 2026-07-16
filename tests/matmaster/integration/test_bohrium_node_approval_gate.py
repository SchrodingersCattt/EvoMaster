import asyncio

import pytest

from matmaster.integration.bohrium_node_approval_gate import (
    BOHRIUM_NODE_START_REVIEW_KIND,
    BridgeBohriumNodeStartApprovalGate,
)
from matmaster.integration.interaction_bridge import InteractionBusyError
from matmaster.types.bohrium_node_approval import BohriumNodeStartRequest


class _FakeBridge:
    def __init__(self, *, reply=None, exc=None):
        self._reply = reply
        self._exc = exc
        self.emitted = []
        self.request_args = None

    async def request(self, **kwargs):
        self.request_args = kwargs
        if self._exc is not None:
            raise self._exc
        return self._reply

    async def emit(self, event):
        self.emitted.append(event)


def _request() -> BohriumNodeStartRequest:
    return BohriumNodeStartRequest(
        request_id="node-review-1",
        trigger_reason="tool:Bash",
        default_lifecycle_policy="keep_running",
        node_sku_id=10,
    )


@pytest.mark.asyncio
async def test_approval_payload_and_selected_lifecycle() -> None:
    bridge = _FakeBridge(
        reply={
            "decision": "start",
            "lifecycle_policy": "idle_timeout",
            "idle_timeout_seconds": 1800,
        }
    )

    decision = await BridgeBohriumNodeStartApprovalGate(bridge).review(_request())

    assert decision.review_outcome == "approved"
    assert decision.lifecycle_policy == "idle_timeout"
    assert decision.idle_timeout_seconds == 1800
    assert bridge.request_args["kind"] == BOHRIUM_NODE_START_REVIEW_KIND
    assert bridge.request_args["payload"] == {
        "schema_version": 1,
        "trigger_reason": "tool:Bash",
        "default_lifecycle_policy": "keep_running",
        "default_idle_timeout_seconds": None,
        "node_sku_id": 10,
    }


@pytest.mark.asyncio
async def test_missing_lifecycle_uses_request_default() -> None:
    decision = await BridgeBohriumNodeStartApprovalGate(
        _FakeBridge(reply={"decision": "start"})
    ).review(_request())

    assert decision.review_outcome == "approved"
    assert decision.lifecycle_policy == "keep_running"


@pytest.mark.asyncio
async def test_reject_invalid_busy_timeout_and_cancel_mapping() -> None:
    rejected = await BridgeBohriumNodeStartApprovalGate(
        _FakeBridge(reply={"decision": "reject"})
    ).review(_request())
    assert rejected.review_outcome == "rejected"

    invalid = await BridgeBohriumNodeStartApprovalGate(
        _FakeBridge(
            reply={
                "decision": "start",
                "lifecycle_policy": "idle_timeout",
                "idle_timeout_seconds": 123,
            }
        )
    ).review(_request())
    assert invalid.review_outcome == "invalid"

    busy = await BridgeBohriumNodeStartApprovalGate(
        _FakeBridge(exc=InteractionBusyError("busy"))
    ).review(_request())
    assert busy.review_outcome == "busy"

    timeout_bridge = _FakeBridge(exc=TimeoutError("timeout"))
    timeout = await BridgeBohriumNodeStartApprovalGate(timeout_bridge).review(
        _request()
    )
    assert timeout.review_outcome == "timeout"
    assert timeout_bridge.emitted[0].kind == BOHRIUM_NODE_START_REVIEW_KIND

    cancelled = await BridgeBohriumNodeStartApprovalGate(
        _FakeBridge(exc=asyncio.CancelledError())
    ).review(_request())
    assert cancelled.review_outcome == "cancelled"
