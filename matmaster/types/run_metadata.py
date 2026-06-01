"""Typed run metadata models for runtime boundaries."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RunIdentity(BaseModel):
    """Runtime identity shared by ExecutionEnvironment and AgentKernelSpec."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = ""
    session_id: str = ""
    spawn_id: str | None = None


class RunMetadata(BaseModel):
    """Passive run identity / directory facts carried by ExecutionEnvironment.

    This type only carries passive identity: ``run_dir`` / ``task_id`` /
    ``source``. Runtime-assembly fields (turn input, user instructions,
    active skills, bohrium rebuild events) belong to
    :class:`~matmaster.core.run_context.AgentRunRequest`, next to the service
    layer that resolves them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_dir: str = ""
    task_id: str = ""
    source: str = ""
