import pytest

from matmaster.tools.builtin.bohrium_tool.submit_review import (
    BohriumSubmitReviewProvider,
    build_review_draft,
    normalize_execution_args,
)
from matmaster.types.submit_review import SubmitReviewArgumentError


def test_draft_none_for_non_submit():
    assert build_review_draft({"action": "query", "job_id": "1"}) is None
    assert build_review_draft({}) is None


def test_draft_adds_defaults_and_cmd_redirect():
    draft = build_review_draft(
        {
            "action": "submit",
            "input_dir": "/share/c",
            "image": "img",
            "cmd": "python run.py",
        }
    )

    assert draft is not None
    assert draft.review_draft_arguments["cmd"] == "python run.py > log 2>&1"
    assert draft.review_draft_arguments["machine"] == "c32_m128_cpu"
    assert draft.review_draft_arguments["job_name"] == "matmaster-job"
    assert draft.review_draft_arguments["disk_size"] == 50
    assert draft.normalization_changes["cmd"]["to"] == "python run.py > log 2>&1"
    assert draft.model_arguments["cmd"] == "python run.py"
    assert draft.draft_issues == []


def test_draft_hides_default_max_runtime_seconds():
    provider = BohriumSubmitReviewProvider(default_max_runtime_seconds=7200)

    draft = provider.build_review_draft(
        {
            "action": "submit",
            "input_dir": "/share/c",
            "image": "img",
            "cmd": "python run.py",
        }
    )

    assert draft is not None
    assert "max_runtime_seconds" not in draft.review_draft_arguments
    assert "max_runtime_seconds" not in draft.editable_fields
    assert "max_runtime_seconds" not in draft.normalization_changes


def test_draft_hides_default_max_wait_time_seconds():
    provider = BohriumSubmitReviewProvider(default_max_wait_time_seconds=900)

    draft = provider.build_review_draft(
        {
            "action": "submit",
            "input_dir": "/share/c",
            "image": "img",
            "cmd": "python run.py",
        }
    )

    assert draft is not None
    assert "max_wait_time_seconds" not in draft.review_draft_arguments
    assert "max_wait_time_seconds" not in draft.editable_fields
    assert "max_wait_time_seconds" not in draft.normalization_changes


def test_explicit_max_runtime_seconds_does_not_override_default():
    provider = BohriumSubmitReviewProvider(default_max_runtime_seconds=7200)

    exec_args = provider.normalize_execution_args(
        {
            "action": "submit",
            "input_dir": "/share/c",
            "image": "i",
            "cmd": "run",
            "max_runtime_seconds": "3600",
        }
    )

    assert exec_args.arguments["max_runtime_seconds"] == 7200
    assert "max_runtime_seconds" not in exec_args.normalization_changes


def test_explicit_max_runtime_seconds_ignored_without_default():
    exec_args = normalize_execution_args(
        {
            "action": "submit",
            "input_dir": "/share/c",
            "image": "i",
            "cmd": "run",
            "max_runtime_seconds": 3600,
        }
    )

    assert "max_runtime_seconds" not in exec_args.arguments
    assert "max_runtime_seconds" not in exec_args.normalization_changes


def test_explicit_max_wait_time_seconds_does_not_override_default():
    provider = BohriumSubmitReviewProvider(default_max_wait_time_seconds=900)

    exec_args = provider.normalize_execution_args(
        {
            "action": "submit",
            "input_dir": "/share/c",
            "image": "i",
            "cmd": "run",
            "max_wait_time_seconds": "600",
        }
    )

    assert exec_args.arguments["max_wait_time_seconds"] == 900
    assert "max_wait_time_seconds" not in exec_args.normalization_changes


def test_explicit_max_wait_time_seconds_ignored_without_default():
    exec_args = normalize_execution_args(
        {
            "action": "submit",
            "input_dir": "/share/c",
            "image": "i",
            "cmd": "run",
            "max_wait_time_seconds": 600,
        }
    )

    assert "max_wait_time_seconds" not in exec_args.arguments
    assert "max_wait_time_seconds" not in exec_args.normalization_changes


def test_draft_missing_required_keeps_issues_still_reviewable():
    draft = build_review_draft(
        {"action": "submit", "input_dir": "/share/c", "cmd": "python run.py"}
    )

    assert draft is not None
    codes = {issue["field"]: issue["code"] for issue in draft.draft_issues}
    assert codes["image"] == "missing_required_field"


def test_draft_oversized_field_raises():
    with pytest.raises(SubmitReviewArgumentError):
        build_review_draft(
            {
                "action": "submit",
                "input_dir": "/share/c",
                "image": "i",
                "cmd": "x" * 9000,
            }
        )


def test_normalize_is_idempotent():
    once = normalize_execution_args(
        {"action": "submit", "input_dir": "/share/c", "image": "i", "cmd": "run"}
    )
    twice = normalize_execution_args(once.arguments)

    assert once.arguments == twice.arguments
    assert twice.normalization_changes == {}


def test_provider_object_implements_protocol():
    provider = BohriumSubmitReviewProvider()

    assert (
        provider.build_review_draft(
            {"action": "submit", "input_dir": "/s", "image": "i", "cmd": "c"}
        )
        is not None
    )
    assert (
        provider.normalize_execution_args({"action": "submit"}).arguments["machine"]
        == "c32_m128_cpu"
    )
