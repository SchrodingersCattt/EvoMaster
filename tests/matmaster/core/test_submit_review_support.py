import json

from matmaster.core.submit_review_support import (
    attach_submit_review_record,
    build_audit_payload,
    build_review_content,
    compute_parameter_changes,
    enforce_submit_review_contract,
    submit_signature,
)
from matmaster.tools.tool_result import ToolResult
from matmaster.types.submit_review import (
    SubmitApprovalGate,
    SubmitExecutionArgs,
    SubmitReviewDecision,
    SubmitReviewDraft,
    SubmitReviewProvider,
    SubmitReviewRequest,
)


def test_submit_review_dataclasses_construct():
    draft = SubmitReviewDraft(
        model_arguments={"action": "submit"},
        review_draft_arguments={"action": "submit", "machine": "c32_m128_cpu"},
        normalization_changes={},
        draft_issues=[],
        editable_fields=["cmd"],
        input_dir="/share/case_001",
    )
    assert draft.file_edit_mode == "live_reported"
    req = SubmitReviewRequest(
        request_id="sr_x",
        tool_name="Bohrium",
        tool_call_id="call_x",
        task_id="t",
        session_id="s",
        draft=draft,
    )
    assert req.timeout_seconds is None
    dec = SubmitReviewDecision(user_decision="submit", review_outcome="approved")
    assert dec.final_arguments is None
    exe = SubmitExecutionArgs(arguments={"action": "submit"}, normalization_changes={})
    assert exe.arguments["action"] == "submit"


def test_protocols_are_runtime_checkable():
    class _P:
        def build_review_draft(self, model_args):
            return None

        def normalize_execution_args(self, args):
            return SubmitExecutionArgs({}, {})

        def blocked_message(self, status):
            return status

    assert isinstance(_P(), SubmitReviewProvider)

    class _G:
        async def review(self, request):
            return SubmitReviewDecision(None, "busy")

    assert isinstance(_G(), SubmitApprovalGate)


def test_submit_signature_stable_and_keyed_on_core_fields():
    args = {
        "input_dir": "/share/c",
        "job_name": "j",
        "image": "i",
        "cmd": "run",
        "machine": "m1",
    }
    changed_machine = {**args, "machine": "m2"}
    changed_cmd = {**args, "cmd": "run2"}

    assert submit_signature(args) == submit_signature(changed_machine)
    assert submit_signature(args) != submit_signature(changed_cmd)


def test_parameter_changes_and_review_content_truncation():
    changes = compute_parameter_changes(
        {"action": "submit", "image": "old", "cmd": "c"},
        {"action": "submit", "image": "new", "cmd": "c"},
    )
    assert changes == {"image": {"from": "old", "to": "new"}}

    review = build_review_content(
        changes,
        [{"relative_path": f"f{i}", "lines": "1"} for i in range(25)],
    )

    assert len(review["input_file_changes"]) == 20
    assert review["input_file_changes_truncated"] is True
    assert build_review_content({}, []) == {}


def test_build_audit_payload_truncates_reported_file_changes():
    changes = [{"relative_path": f"f{i}", "lines": "1"} for i in range(205)]

    audit = build_audit_payload(
        request_id="sr_1",
        session_id="s",
        task_id="t",
        tool_call_id="call_1",
        review_outcome="approved",
        user_decision="submit",
        model_arguments={"action": "submit"},
        review_draft_arguments={"action": "submit", "cmd": "c"},
        final_arguments={"action": "submit", "cmd": "c2"},
        execution_arguments={"action": "submit", "cmd": "c2 > log 2>&1"},
        normalization_changes={},
        user_parameter_changes={"cmd": {"from": "c", "to": "c2"}},
        execution_normalization_changes={"cmd": {"from": "c2", "to": "c2 > log"}},
        reported_input_file_changes=changes,
        reported_input_file_change_count=len(changes),
        execution_audit={"execution_attempted": True},
    )

    assert audit["schema_version"] == 1
    assert audit["changed_fields"] == ["cmd"]
    assert audit["reported_input_file_change_count"] == 205
    assert audit["reported_input_file_changes_truncated"] is True
    assert len(audit["reported_input_file_changes"]) == 200
    assert audit["input_file_changes_source"] == "frontend_reported"
    assert audit["execution_attempted"] is True


def test_attach_injects_review_and_payload_and_meta():
    result = ToolResult(
        status="blocked",
        content=json.dumps({"success": False, "status": "UserRejected"}),
    )
    review = {"parameter_changes": {"cmd": {"from": "a", "to": "b"}}}
    audit = {"schema_version": 1, "request_id": "sr_1"}

    out = attach_submit_review_record(
        result,
        review,
        audit,
        block_reason="UserRejected",
    )

    body = json.loads(out.content)
    assert body["review"]["parameter_changes"]["cmd"]["to"] == "b"
    assert out.payload["bohrium_submit_review"]["request_id"] == "sr_1"
    assert out.meta["block_reason"] == "UserRejected"
    assert out.meta["layer"] == "submit_approval_gate"


def test_enforce_restores_destroyed_record():
    result = ToolResult(
        status="success",
        content=json.dumps({"success": True, "job_id": "1"}),
    )
    review = {"parameter_changes": {"cmd": {"from": "a", "to": "b"}}}
    audit = {"schema_version": 1, "request_id": "sr_1"}

    out = enforce_submit_review_contract(result, review, audit)

    assert json.loads(out.content)["review"]["parameter_changes"]["cmd"]["to"] == "b"
    assert out.payload["bohrium_submit_review"]["request_id"] == "sr_1"


def test_no_file_content_leakage():
    review = build_review_content({}, [{"relative_path": "f", "lines": "1"}])

    assert "SECRET" not in json.dumps(review)
    assert set(review["input_file_changes"][0]) == {"relative_path", "lines"}
