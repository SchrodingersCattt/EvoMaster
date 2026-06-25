from __future__ import annotations

from typing import Any

from matmaster.types.submit_review import (
    SubmitExecutionArgs,
    SubmitReviewArgumentError,
    SubmitReviewDraft,
)

DEFAULT_MACHINE = "c32_m128_cpu"
DEFAULT_JOB_NAME = "matmaster-job"
DEFAULT_DISK_SIZE = 50
CMD_LOG_SUFFIX = "> log 2>&1"
EDITABLE_FIELDS = [
    "input_dir",
    "image",
    "cmd",
    "machine",
    "job_name",
    "disk_size",
]
SUBMIT_FIELDS = [
    "action",
    "input_dir",
    "image",
    "cmd",
    "machine",
    "job_name",
    "disk_size",
]
_MAX_LEN = {
    "cmd": 8192,
    "image": 512,
    "machine": 128,
    "job_name": 256,
    "input_dir": 2048,
}
_BLOCK_MESSAGES = {
    "UserRejected": (
        "用户拒绝了本次 Bohrium 提交。请不要重新提交本作业，总结当前进展、并结束本轮等待用户反馈。"
    ),
    "ReviewTimeout": (
        "本次提交未在限定时间内获得用户确认，未提交。请不要重新提交本作业，"
        "可总结进展或转做其它工作。"
    ),
    "ReviewBusy": (
        "当前已有待处理的交互，本次提交未发起确认，未提交。"
        "请稍后由用户处理后再继续，不要重复提交。"
    ),
    "SupersededByPriorEdit": (
        "The user modified the parameters or input files "
        "of another submit in the same batch. This submit "
        "was not executed; please refer to those changes "
        "and re-evaluate before resubmitting."
    ),
    "ResubmitBlocked": (
        "本作业已被拒绝/未获确认，请勿重复提交；" "可总结进展或转做其它工作。"
    ),
}


def oversized_submit_fields(args: Any) -> list[str]:
    """返回超过长度上限的 submit 字段名。"""
    if not isinstance(args, dict):
        return []
    return sorted(
        field
        for field, max_len in _MAX_LEN.items()
        if isinstance(args.get(field), str) and len(args[field]) > max_len
    )


def _canonicalize_submit_args(
    args: dict[str, Any],
    *,
    default_max_runtime_seconds: int | None = None,
    include_runtime_policy: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    canonical: dict[str, Any] = {key: args[key] for key in SUBMIT_FIELDS if key in args}
    changes: dict[str, Any] = {}

    if not canonical.get("machine"):
        changes["machine"] = {"from": canonical.get("machine"), "to": DEFAULT_MACHINE}
        canonical["machine"] = DEFAULT_MACHINE
    if not canonical.get("job_name"):
        changes["job_name"] = {
            "from": canonical.get("job_name"),
            "to": DEFAULT_JOB_NAME,
        }
        canonical["job_name"] = DEFAULT_JOB_NAME

    raw_disk = canonical.get("disk_size")
    if raw_disk in (None, ""):
        changes["disk_size"] = {"from": raw_disk, "to": DEFAULT_DISK_SIZE}
        canonical["disk_size"] = DEFAULT_DISK_SIZE
    else:
        try:
            disk_size = int(raw_disk)
        except (TypeError, ValueError) as exc:
            raise SubmitReviewArgumentError("disk_size must be an integer") from exc
        if raw_disk != disk_size:
            changes["disk_size"] = {"from": raw_disk, "to": disk_size}
        canonical["disk_size"] = disk_size

    if include_runtime_policy and default_max_runtime_seconds is not None:
        canonical["max_runtime_seconds"] = default_max_runtime_seconds

    cmd = canonical.get("cmd")
    if cmd:
        stripped = str(cmd).rstrip()
        if not stripped.endswith(CMD_LOG_SUFFIX):
            normalized_cmd = f"{stripped} {CMD_LOG_SUFFIX}"
            changes["cmd"] = {"from": cmd, "to": normalized_cmd}
            canonical["cmd"] = normalized_cmd
        else:
            canonical["cmd"] = stripped
            if cmd != stripped:
                changes["cmd"] = {"from": cmd, "to": stripped}
    return canonical, changes


def build_review_draft(
    model_args: Any,
    *,
    default_max_runtime_seconds: int | None = None,
) -> SubmitReviewDraft | None:
    """构造 submit_review 展示草稿；None 只表示非 submit。"""
    if not isinstance(model_args, dict) or model_args.get("action") != "submit":
        return None

    oversized = oversized_submit_fields(model_args)
    if oversized:
        raise SubmitReviewArgumentError(
            f"submit argument(s) too long: {', '.join(oversized)}"
        )

    canonical, changes = _canonicalize_submit_args(
        model_args,
        default_max_runtime_seconds=default_max_runtime_seconds,
        include_runtime_policy=False,
    )
    issues: list[dict[str, Any]] = []
    for field in ("input_dir", "image", "cmd"):
        if not model_args.get(field):
            issues.append(
                {
                    "field": field,
                    "code": "missing_required_field",
                    "message": f"{field} is required before submit.",
                }
            )

    return SubmitReviewDraft(
        model_arguments=dict(model_args),
        review_draft_arguments=canonical,
        normalization_changes=changes,
        draft_issues=issues,
        editable_fields=list(EDITABLE_FIELDS),
        input_dir=str(model_args.get("input_dir") or ""),
        file_edit_mode="live_reported",
    )


def normalize_execution_args(
    args: Any,
    *,
    default_max_runtime_seconds: int | None = None,
) -> SubmitExecutionArgs:
    """执行前严格、幂等、无副作用地规范化 submit 参数。"""
    oversized = oversized_submit_fields(args)
    if oversized:
        raise SubmitReviewArgumentError(
            f"submit argument(s) too long: {', '.join(oversized)}"
        )
    canonical, changes = _canonicalize_submit_args(
        dict(args) if isinstance(args, dict) else {},
        default_max_runtime_seconds=default_max_runtime_seconds,
    )
    return SubmitExecutionArgs(arguments=canonical, normalization_changes=changes)


class BohriumSubmitReviewProvider:
    """无状态 Bohrium submit review provider。"""

    def __init__(self, *, default_max_runtime_seconds: int | None = None) -> None:
        self._default_max_runtime_seconds = default_max_runtime_seconds

    def build_review_draft(
        self, model_args: dict[str, Any]
    ) -> SubmitReviewDraft | None:
        return build_review_draft(
            model_args,
            default_max_runtime_seconds=self._default_max_runtime_seconds,
        )

    def normalize_execution_args(self, args: dict[str, Any]) -> SubmitExecutionArgs:
        return normalize_execution_args(
            args,
            default_max_runtime_seconds=self._default_max_runtime_seconds,
        )

    def blocked_message(self, status: str) -> str:
        return _BLOCK_MESSAGES[status]
