from __future__ import annotations

import asyncio
from typing import Any

from matmaster.integration.interaction_bridge import (
    InteractionBridge,
    InteractionBusyError,
)
from matmaster.types import InteractionTimeoutEvent
from matmaster.types.submit_review import SubmitReviewDecision, SubmitReviewRequest

SUBMIT_REVIEW_KIND = "submit_review"
SUBMIT_REVIEW_SCHEMA_VERSION = 1


def _draft_to_payload(request: SubmitReviewRequest) -> dict[str, Any]:
    draft = request.draft
    return {
        "schema_version": SUBMIT_REVIEW_SCHEMA_VERSION,
        "tool_name": request.tool_name,
        "tool_call_id": request.tool_call_id,
        "model_arguments": draft.model_arguments,
        "review_draft_arguments": draft.review_draft_arguments,
        "normalization_changes": draft.normalization_changes,
        "draft_issues": draft.draft_issues,
        "editable_fields": draft.editable_fields,
        "input_dir": draft.input_dir,
        "file_edit_mode": draft.file_edit_mode,
    }


def _reply_to_decision(reply: dict[str, Any]) -> SubmitReviewDecision:
    decision = reply.get("decision")
    if decision == "submit":
        outcome = "approved"
    elif decision == "reject":
        outcome = "rejected"
    else:
        outcome = "rejected"

    return SubmitReviewDecision(
        user_decision=decision if decision in ("submit", "reject") else None,
        review_outcome=outcome,
        final_arguments=reply.get("submit_arguments"),
        reported_input_file_changes=reply.get("reported_input_file_changes"),
    )


class BridgeSubmitApprovalGate:
    """submit_review 接入层 adapter：复用通用 InteractionBridge。"""

    def __init__(self, bridge: InteractionBridge) -> None:
        self._bridge = bridge

    async def review(self, request: SubmitReviewRequest) -> SubmitReviewDecision:
        try:
            reply = await self._bridge.request(
                kind=SUBMIT_REVIEW_KIND,
                request_id=request.request_id,
                payload=_draft_to_payload(request),
                timeout_seconds=request.timeout_seconds,
            )
        except InteractionBusyError:
            return SubmitReviewDecision(user_decision=None, review_outcome="busy")
        except TimeoutError:
            await self._emit_timeout(request.request_id)
            return SubmitReviewDecision(user_decision=None, review_outcome="timeout")
        except asyncio.CancelledError:
            return SubmitReviewDecision(user_decision=None, review_outcome="cancelled")
        return _reply_to_decision(reply)

    async def _emit_timeout(self, request_id: str) -> None:
        await self._bridge.emit(
            InteractionTimeoutEvent(
                source="System",
                kind=SUBMIT_REVIEW_KIND,
                request_id=request_id,
            )
        )
