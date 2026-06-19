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

    assert isinstance(_P(), SubmitReviewProvider)

    class _G:
        async def review(self, request):
            return SubmitReviewDecision(None, "busy")

    assert isinstance(_G(), SubmitApprovalGate)
