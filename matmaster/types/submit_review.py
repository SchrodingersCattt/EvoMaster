from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


class SubmitReviewArgumentError(ValueError):
    """submit 参数非法时硬失败，不进入 review，也不继续执行 submit。"""


@dataclass
class SubmitReviewDraft:
    """展示型草稿，供 provider.build_review_draft 产出。"""

    model_arguments: dict[str, Any]
    review_draft_arguments: dict[str, Any]
    normalization_changes: dict[str, Any]
    draft_issues: list[dict[str, Any]]
    editable_fields: list[str]
    input_dir: str
    file_edit_mode: str = "live_reported"


@dataclass
class SubmitExecutionArgs:
    """严格规范化后的执行参数，供 provider.normalize_execution_args 产出。"""

    arguments: dict[str, Any]
    normalization_changes: dict[str, Any]


@dataclass
class SubmitReviewRequest:
    """runner 交给 gate 的审阅请求。"""

    request_id: str
    tool_name: str
    tool_call_id: str
    task_id: str
    session_id: str
    draft: SubmitReviewDraft
    timeout_seconds: int | None = None


@dataclass
class SubmitReviewDecision:
    """gate 回给 runner 的决定。"""

    user_decision: str | None
    review_outcome: str
    final_arguments: dict[str, Any] | None = None
    reported_input_file_changes: list[dict[str, Any]] | None = None


@runtime_checkable
class SubmitReviewProvider(Protocol):
    """工具侧提交语义知识：draft + 幂等严格规范化。"""

    def build_review_draft(
        self, model_args: dict[str, Any]
    ) -> SubmitReviewDraft | None: ...

    def normalize_execution_args(self, args: dict[str, Any]) -> SubmitExecutionArgs: ...


@runtime_checkable
class SubmitApprovalGate(Protocol):
    """接入层：发起提交审阅并阻塞等待决定。"""

    async def review(self, request: SubmitReviewRequest) -> SubmitReviewDecision: ...
