from __future__ import annotations

import hashlib
import json
from typing import Any

from matmaster.tools.tool_result import ToolResult

SUBMIT_APPROVAL_GATE_KEY = "submit_approval_gate"
RUN_IDENTITY_KEY = "run_identity"
RESUBMIT_SIGNATURES_KEY = "bohrium_submit_resubmit_signatures"
SUBMIT_REVIEW_RECORDS_KEY = "submit_review_records"
SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY = "bohrium_submit_skip_confirmation"

MAX_CONTENT_FILE_CHANGES = 20
MAX_PAYLOAD_FILE_CHANGES = 200
_SIGNATURE_FIELDS = ("input_dir", "job_name", "image", "cmd")


def submit_signature(args: dict[str, Any]) -> str:
    key = {field: str(args.get(field) or "").strip() for field in _SIGNATURE_FIELDS}
    return hashlib.sha256(
        json.dumps(key, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def compute_parameter_changes(
    draft_args: dict[str, Any],
    final_args: dict[str, Any],
) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for key in sorted(set(draft_args) | set(final_args)):
        before = draft_args.get(key)
        after = final_args.get(key)
        if before != after:
            changes[key] = {"from": before, "to": after}
    return changes


def build_review_content(
    parameter_changes: dict[str, Any],
    input_file_changes: list[dict[str, Any]],
) -> dict[str, Any]:
    review: dict[str, Any] = {}
    if parameter_changes:
        review["parameter_changes"] = parameter_changes
    if input_file_changes:
        review["input_file_changes"] = [
            {
                "relative_path": change.get("relative_path"),
                "lines": change.get("lines"),
            }
            for change in input_file_changes[:MAX_CONTENT_FILE_CHANGES]
        ]
        if len(input_file_changes) > MAX_CONTENT_FILE_CHANGES:
            review["input_file_changes_truncated"] = True
    return review


def build_audit_payload(
    *,
    request_id: str,
    session_id: str,
    task_id: str,
    tool_call_id: str,
    review_outcome: str,
    user_decision: str | None,
    model_arguments: dict[str, Any],
    review_draft_arguments: dict[str, Any],
    final_arguments: dict[str, Any],
    execution_arguments: dict[str, Any] | None,
    normalization_changes: dict[str, Any],
    user_parameter_changes: dict[str, Any],
    execution_normalization_changes: dict[str, Any],
    reported_input_file_changes: list[dict[str, Any]],
    reported_input_file_change_count: int,
    execution_audit: dict[str, Any] | None,
) -> dict[str, Any]:
    audit = {
        "schema_version": 1,
        "request_id": request_id,
        "session_id": session_id,
        "task_id": task_id,
        "tool_call_id": tool_call_id,
        "review_outcome": review_outcome,
        "user_decision": user_decision,
        "model_arguments": model_arguments,
        "review_draft_arguments": review_draft_arguments,
        "final_arguments": final_arguments,
        "execution_arguments": execution_arguments or {},
        "normalization_changes": normalization_changes,
        "user_parameter_changes": user_parameter_changes,
        "execution_normalization_changes": execution_normalization_changes,
        "changed_fields": list(user_parameter_changes.keys()),
        "reported_input_file_change_count": reported_input_file_change_count,
        "reported_input_file_changes_truncated": (
            reported_input_file_change_count > MAX_PAYLOAD_FILE_CHANGES
        ),
        "reported_input_file_changes": reported_input_file_changes[
            :MAX_PAYLOAD_FILE_CHANGES
        ],
        "input_file_changes_source": "frontend_reported",
    }
    audit.update(
        execution_audit
        or {"execution_attempted": False, "external_effect_started": False}
    )
    return audit


def _apply_record(
    result: ToolResult,
    review_content: dict[str, Any],
    audit_payload: dict[str, Any],
    *,
    block_reason: str | None,
) -> ToolResult:
    try:
        body = json.loads(result.content) if result.content else {}
        if not isinstance(body, dict):
            body = {"message": result.content}
    except (TypeError, ValueError):
        body = {"message": result.content}

    if review_content:
        body["review"] = review_content
    else:
        body.pop("review", None)

    new_meta = dict(result.meta)
    new_meta.pop("submit_execution_audit", None)
    if block_reason:
        new_meta["block_reason"] = block_reason
        new_meta["layer"] = "submit_approval_gate"

    return result.model_copy(
        update={
            "content": json.dumps(body, ensure_ascii=False),
            "payload": {
                **result.payload,
                "bohrium_submit_review": audit_payload,
            },
            "meta": new_meta,
        }
    )


def attach_submit_review_record(
    result: ToolResult,
    review_content: dict[str, Any],
    audit_payload: dict[str, Any],
    *,
    block_reason: str | None = None,
) -> ToolResult:
    return _apply_record(
        result,
        review_content,
        audit_payload,
        block_reason=block_reason,
    )


def enforce_submit_review_contract(
    result: ToolResult,
    review_content: dict[str, Any],
    audit_payload: dict[str, Any],
) -> ToolResult:
    return _apply_record(
        result,
        review_content,
        audit_payload,
        block_reason=None,
    )


def install_submit_review_hooks(
    *,
    runner_state: Any,
    hook_executor: Any,
    run_identity: Any,
    submit_approval_gate: Any,
) -> None:
    """Wire submit review capability into runner state and POST hook rewrites."""
    from matmaster.core.hooks import HookEvent

    runner_state.set(SUBMIT_APPROVAL_GATE_KEY, submit_approval_gate)
    runner_state.set(RUN_IDENTITY_KEY, run_identity)

    def _merge_execution_audit(
        audit_baseline: dict[str, Any],
        result: Any,
    ) -> dict[str, Any]:
        execution_audit = (result.meta or {}).get("submit_execution_audit")
        if not execution_audit:
            return audit_baseline
        return {**audit_baseline, **execution_audit}

    def _record_for(tool_call_id: str) -> dict[str, Any] | None:
        records = runner_state.get(SUBMIT_REVIEW_RECORDS_KEY) or {}
        return records.get(tool_call_id)

    async def _attach_post(ctx, result):
        record = _record_for(ctx.tool_call_id)
        if record is None:
            return None
        audit = _merge_execution_audit(record["audit_baseline"], result)
        record["audit_baseline"] = audit
        return attach_submit_review_record(
            result,
            record["review_content"],
            audit,
        )

    async def _enforce_post(ctx, result):
        record = _record_for(ctx.tool_call_id)
        if record is None:
            return None
        audit = _merge_execution_audit(record["audit_baseline"], result)
        return enforce_submit_review_contract(
            result,
            record["review_content"],
            audit,
        )

    hook_executor.rewrite(HookEvent.POST_TOOL_CALL, _attach_post)
    hook_executor.rewrite(HookEvent.POST_TOOL_CALL, _enforce_post)
