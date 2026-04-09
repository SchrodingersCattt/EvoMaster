from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ExecutionContextLike(Protocol):
    session_type: str
    execution_workdir: str
    remote_workspace_root: str
    remote_project_root: str
    node_id: int | None
    node_ip: str | None
    ssh_attached: bool


class SubmissionSpecLike(Protocol):
    executor: dict[str, Any] | None
    storage: dict[str, Any] | None
    submission_mode: str


@dataclass(frozen=True)
class SubmissionRequest:
    executor_template: dict[str, Any] | None
    needs_storage: bool
    submission_mode: str
