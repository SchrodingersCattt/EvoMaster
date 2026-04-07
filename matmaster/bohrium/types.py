from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict


@dataclass(frozen=True)
class BohriumCredentials:
    access_key: str
    project_id: int
    user_id: int | None
    user_no: str
    base_url: str

    @classmethod
    def from_mapping(cls, values: dict[str, Any]) -> "BohriumCredentials":
        access_key = str(values.get("access_key") or "").strip()
        raw_project_id = values.get("project_id", -1)
        raw_user_id = values.get("user_id")
        try:
            project_id = int(raw_project_id)
        except (TypeError, ValueError):
            project_id = -1
        try:
            user_id = int(raw_user_id) if raw_user_id not in (None, "", -1) else None
        except (TypeError, ValueError):
            user_id = None
        user_no = str(values.get("user_no") or "").strip()
        base_url = str(values.get("base_url") or "").strip().rstrip("/")
        return cls(
            access_key=access_key,
            project_id=project_id,
            user_id=user_id,
            user_no=user_no,
            base_url=base_url,
        )


class BohriumRuntimeSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_type: str
    execution_workdir: str
    remote_workspace_root: str
    remote_project_root: str
    node_id: int | None = None
    node_ip: str | None = None
    ssh_attached: bool = False


@dataclass(frozen=True)
class BohriumExecutionContext:
    session_type: str
    execution_workdir: str
    remote_workspace_root: str
    remote_project_root: str
    node_id: int | None
    node_ip: str | None
    ssh_attached: bool


@dataclass(frozen=True)
class BohriumSubmissionSpec:
    executor: dict[str, Any] | None
    storage: dict[str, Any] | None
    submission_mode: str
