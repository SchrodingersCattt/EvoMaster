"""Typed run metadata models for runtime boundaries."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RunIdentity(BaseModel):
    """Runtime identity shared by ExecutionEnvironment and AgentRuntimeSpec."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = ""
    session_id: str = ""
    spawn_id: str | None = None


class RunMetadata(BaseModel):
    """Passive run identity / directory facts carried by ExecutionEnvironment.

    Phase 3 slimmed this back to its true passive identity: ``run_dir`` /
    ``task_id`` / ``source``. The former runtime-assembly fields (turn input,
    user instructions, active skills, bohrium rebuild events) moved to
    :class:`~matmaster.core.run_context.AgentRunRequest`, next to the service
    layer that resolves them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_dir: str = ""
    task_id: str = ""
    source: str = ""
