"""Helpers for constructing run identity values."""

from __future__ import annotations

from matmaster.core.run_context import AgentRunContext
from matmaster.types.run_metadata import RunIdentity


def build_run_identity(
    ctx: AgentRunContext,
    *,
    spawn_id: str | None,
) -> RunIdentity:
    return RunIdentity(
        task_id=ctx.environment.metadata.task_id,
        session_id=ctx.environment.session_id,
        spawn_id=spawn_id,
    )
