import asyncio

import pytest

from matmaster.integration.interaction_bridge import InteractionBusyError
from matmaster.integration.submit_approval_gate import (
    BridgeSubmitApprovalGate,
    _draft_to_payload,
)
from matmaster.types.submit_review import SubmitReviewDraft, SubmitReviewRequest


def _req():
    draft = SubmitReviewDraft(
        model_arguments={"action": "submit", "cmd": "run"},
        review_draft_arguments={
            "action": "submit",
            "cmd": "run > log 2>&1",
            "machine": "c32_m128_cpu",
        },
        normalization_changes={"cmd": {"from": "run", "to": "run > log 2>&1"}},
        draft_issues=[],
        editable_fields=["cmd"],
        input_dir="/share/c",
    )
    return SubmitReviewRequest(
        request_id="sr_1",
        tool_name="Bohrium",
        tool_call_id="call_1",
        task_id="t",
        session_id="s",
        draft=draft,
    )


class _FakeBridge:
    def __init__(self, *, reply=None, exc=None):
        self._reply = reply
        self._exc = exc
        self.emitted = []
        self.last_payload = None

    async def request(self, *, kind, request_id, payload, timeout_seconds=None):
        self.last_payload = payload
        if self._exc is not None:
            raise self._exc
        return self._reply

    async def emit(self, event):
        self.emitted.append(event)


def test_draft_to_payload_shape():
    payload = _draft_to_payload(_req())

    assert payload["schema_version"] == 1
    assert payload["tool_name"] == "Bohrium"
    assert payload["tool_call_id"] == "call_1"
    assert payload["review_draft_arguments"]["cmd"] == "run > log 2>&1"
    assert payload["editable_fields"] == ["cmd"]
    assert payload["file_edit_mode"] == "live_reported"
    assert "session_id" not in payload


@pytest.mark.asyncio
async def test_approved_and_rejected():
    gate = BridgeSubmitApprovalGate(
        _FakeBridge(
            reply={
                "decision": "submit",
                "submit_arguments": {
                    "action": "submit",
                    "cmd": "run > log 2>&1",
                },
                "reported_input_file_changes": [{"relative_path": "a", "lines": "1"}],
            }
        )
    )

    decision = await gate.review(_req())

    assert decision.review_outcome == "approved"
    assert decision.user_decision == "submit"
    assert decision.final_arguments["cmd"] == "run > log 2>&1"
    assert decision.reported_input_file_changes == [
        {"relative_path": "a", "lines": "1"}
    ]

    rejected_gate = BridgeSubmitApprovalGate(
        _FakeBridge(reply={"decision": "reject", "submit_arguments": {}})
    )
    rejected = await rejected_gate.review(_req())
    assert rejected.review_outcome == "rejected"
    assert rejected.user_decision == "reject"


@pytest.mark.asyncio
async def test_busy_timeout_cancel_mapping():
    busy_gate = BridgeSubmitApprovalGate(_FakeBridge(exc=InteractionBusyError("x")))
    assert (await busy_gate.review(_req())).review_outcome == "busy"

    timeout_bridge = _FakeBridge(exc=TimeoutError("x"))
    timeout_decision = await BridgeSubmitApprovalGate(timeout_bridge).review(_req())
    assert timeout_decision.review_outcome == "timeout"
    assert len(timeout_bridge.emitted) == 1
    assert timeout_bridge.emitted[0].kind == "submit_review"

    cancelled_gate = BridgeSubmitApprovalGate(_FakeBridge(exc=asyncio.CancelledError()))
    cancelled = await cancelled_gate.review(_req())
    assert cancelled.review_outcome == "cancelled"
