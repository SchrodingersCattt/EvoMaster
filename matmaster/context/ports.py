from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
JsonObject: TypeAlias = Mapping[str, JsonValue]


@dataclass(frozen=True)
class UserInstructions:
    text: str
    hash: str
    truncated: bool = False


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


SkillResolver: TypeAlias = Callable[
    [tuple[SessionEvent, ...]], tuple[ActiveSkill, ...]
]


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
class SessionJobs:
    active_jobs: tuple[JsonObject, ...] = ()

    @classmethod
    def empty(cls) -> SessionJobs:
        return cls(active_jobs=())


@dataclass(frozen=True)
class SessionJobsQuery:
    session_id: str


class SessionJobsPort(Protocol):
    async def load_session_jobs(
        self,
        query: SessionJobsQuery,
    ) -> SessionJobs:
        raise NotImplementedError


@dataclass(frozen=True)
class ContextAssemblyPorts:
    session_events: SessionEventsPort
    session_jobs: SessionJobsPort | None = None
