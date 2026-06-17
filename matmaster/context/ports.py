from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypeAlias, runtime_checkable

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
JsonObject: TypeAlias = Mapping[str, JsonValue]


@dataclass(frozen=True)
class UserInstructions:
    text: str
    hash: str
    truncated: bool = False

    @classmethod
    def empty(cls) -> UserInstructions:
        return cls(text="", hash=hash_user_instructions(""), truncated=False)


def hash_user_instructions(text: str) -> str:
    """Canonical AGENT.md content hash: ``sha256:`` + hex of raw utf-8 bytes.

    Defined here so the runtime and service layers share one implementation.
    The hash drives anchor-vs-continuation turn intent, so any divergence would
    silently break AGENT.md change detection. Operates on raw text (no strip):
    trailing-whitespace changes must still produce a new hash.
    """
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


class UserInstructionsPort(Protocol):
    async def load_user_instructions(
        self,
        workspace_root: Path,
    ) -> UserInstructions:
        raise NotImplementedError


@dataclass(frozen=True)
class ActiveSkill:
    """matmaster-owned DTO for prompt-side skill rendering.

    Service layer is responsible for resolving skill_hit events into this
    structure, including lookup, disabled rules, and local/remote roots.
    """

    name: str
    description: str = ""
    mcp_server: str | None = None


@dataclass(frozen=True)
class SessionEvent:
    """DB events row envelope for context assembly.

    `content` must preserve the raw DB payload shape after JSON parsing. For
    rows loaded through service-layer codecs, nested lists are converted to
    tuples by `freeze_json_object`; callers should not pass display-flattened
    User/query rows where files/images/workspace_paths were hoisted out.
    """

    id: int
    event_type: str
    source: str | None
    content: JsonObject
    task_id: str | None = None
    invocation_id: str | None = None
    spawn_id: str | None = None
    created_at_ms: int | None = None


SkillResolver: TypeAlias = Callable[[tuple[SessionEvent, ...]], tuple[ActiveSkill, ...]]


@dataclass(frozen=True)
class SessionEventQuery:
    session_id: str
    spawn_id: str | None
    until_event_id: int | None = None
    event_types: tuple[str, ...] | None = None
    limit: int | None = None
    order: Literal["asc", "desc"] = "asc"


class SessionEventsPort(Protocol):
    async def load_events(
        self,
        query: SessionEventQuery,
    ) -> tuple[SessionEvent, ...]:
        raise NotImplementedError


@dataclass(frozen=True)
class WorkspaceJobsExport:
    path: str
    format: Literal["csv"]
    row_count: int
    columns: tuple[str, ...]
    reason: Literal["row_limit", "char_limit"]


@dataclass(frozen=True)
class WorkspaceJobsSummary:
    total: int  # == active + unhandled_terminal + handled_recent_terminal == snapshot rows
    active: int
    unhandled_terminal: int
    handled_recent_terminal: int
    by_status: Mapping[str, int]
    failed: int
    stopped: int
    lost: int
    unhandled_action: int


@dataclass(frozen=True)
class WorkspaceJobsExportError:
    reason: Literal[
        "session_missing", "bad_target_path", "write_failed", "serialize_failed"
    ]
    rows: int
    target_path: str


@dataclass(frozen=True)
class WorkspaceJobs:
    workspace: str | None = None
    active_jobs: tuple[JsonObject, ...] = ()
    unhandled_terminal_jobs: tuple[JsonObject, ...] = ()
    handled_recent_terminal_jobs: tuple[JsonObject, ...] = ()
    mode: Literal["workspace_observation", "session_workspace_delivery"] | None = None
    summary: WorkspaceJobsSummary | None = None
    export: WorkspaceJobsExport | None = None
    export_error: WorkspaceJobsExportError | None = None
    required_error: Mapping[str, JsonValue] | None = None
    preview_limit: int | None = None
    preview_rows: tuple[JsonObject, ...] = ()
    omitted_count: int | None = None
    required_truncated: bool = False
    handled_recent_has_more: bool = False
    handled_recent_unavailable: bool = False

    @classmethod
    def empty(cls) -> WorkspaceJobs:
        return cls()


@dataclass(frozen=True)
class WorkspaceJobsQuery:
    session_id: str


@runtime_checkable
class WorkspaceJobsPort(Protocol):
    async def load_workspace_jobs(
        self,
        query: WorkspaceJobsQuery,
    ) -> WorkspaceJobs:
        raise NotImplementedError


@runtime_checkable
class BohriumJobLedgerPort(Protocol):
    """Sync write-side port: BohriumTool 同步 Bohrium 作业生命周期到 ledger。"""

    def record_submit(
        self,
        *,
        job_id: str,
        job_name: str | None,
        project_id: int,
        sandbox: bool,
        input_dir: str,
    ) -> None:
        raise NotImplementedError

    def record_poll(
        self,
        *,
        job_id: str,
        sandbox: bool,
        status_code: int,
    ) -> None:
        raise NotImplementedError

    def record_kill(self, *, job_id: str, sandbox: bool) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class ContextAssemblyPorts:
    session_events: SessionEventsPort
    workspace_jobs: WorkspaceJobsPort | None = None
