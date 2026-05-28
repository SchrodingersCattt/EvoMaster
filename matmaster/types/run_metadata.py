"""Typed run metadata models for runtime boundaries."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from matmaster.context.ports import UserInstructions
from matmaster.context.sources.turn_input import TurnInput

BohriumRebuildEvent = dict[str, Any]


class RunIdentity(BaseModel):
    """Runtime identity shared by PlaygroundContext and AgentRuntimeSpec."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = ""
    session_id: str = ""
    spawn_id: str | None = None


class RunMetadata(BaseModel):
    """Typed metadata carried by PlaygroundContext during one run."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    run_dir: str = ""
    task_id: str = ""
    source: str = ""
    turn_input: TurnInput | None = None
    user_instructions: UserInstructions | None = None
    active_skills: frozenset[str] = Field(default_factory=frozenset)
    bohrium_rebuild_events: tuple[BohriumRebuildEvent, ...] = Field(
        default_factory=tuple
    )
