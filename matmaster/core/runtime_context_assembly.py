"""Runtime context assembly wiring for Exp.build_runtime()."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from matmaster.context.assembly import ContextAssembler, ContextRenderOptions
from matmaster.context.compaction import ContextCompactor
from matmaster.context.ports import (
    ActiveSkill,
    ContextAssemblyPorts,
    SessionEvent,
    SessionJobs,
    SessionJobsQuery,
    SkillResolver,
    UserInstructions,
    hash_user_instructions,
)
from matmaster.context.session import SessionContextBuilder
from matmaster.core.playground import PlaygroundContext
from matmaster.types.runtime import AgentRuntimeSpec
from matmaster.types.runtime_ports import EmptySessionEventHistory

SessionContextFactory = Callable[[tuple[SessionEvent, ...]], SessionContextBuilder]


@dataclass(frozen=True)
class RuntimeContextAssembly:
    compactor: ContextCompactor | None = None
    context_assembler: ContextAssembler | None = None
    assembly_ports: ContextAssemblyPorts | None = None


def empty_skill_resolver(_events: tuple[SessionEvent, ...]) -> tuple[ActiveSkill, ...]:
    """Default SkillResolver for paths that do not wire one explicitly."""
    return ()


def build_session_context_factory(
    *,
    skill_resolver: SkillResolver,
) -> SessionContextFactory:
    def factory(events: tuple[SessionEvent, ...]) -> SessionContextBuilder:
        return SessionContextBuilder(
            events=events,
            active_skills=skill_resolver(events),
        )

    return factory


class _EmptySessionJobsPort:
    async def load_session_jobs(self, query: SessionJobsQuery) -> SessionJobs:
        return SessionJobs.empty()


def build_runtime_context_assembly(
    *,
    spec: AgentRuntimeSpec,
    ctx: PlaygroundContext,
    skill_resolver: SkillResolver,
    spawn_id: str | None,
    logger: logging.Logger,
) -> RuntimeContextAssembly:
    """Build context assembler and compactor resources for runtime execution."""
    if spec.llm_provider is None:
        return RuntimeContextAssembly()

    history_port = ctx.runtime_ports.compaction.history
    if history_port is None:
        history_port = EmptySessionEventHistory()

    user_instructions = ctx.metadata.user_instructions or UserInstructions(
        text="",
        hash=hash_user_instructions(""),
        truncated=False,
    )
    assembly_ports = ContextAssemblyPorts(
        session_events=history_port,
        session_jobs=_EmptySessionJobsPort(),
    )
    context_assembler = ContextAssembler(
        ports=assembly_ports,
        session_context_factory=build_session_context_factory(
            skill_resolver=skill_resolver,
        ),
        render_options=ContextRenderOptions(),
    )

    return RuntimeContextAssembly(
        compactor=ContextCompactor(
            config=spec.compaction,
            context_assembler=context_assembler,
            user_instructions=user_instructions,
            session_id=ctx.session_id,
            spawn_id=spawn_id,
            runtime_covered_until_provider=history_port.latest_scope_event_id,
            event_sink=None,
            compaction_scope=f'{ctx.metadata.task_id}:{spawn_id or "root"}',
        ),
        context_assembler=context_assembler,
        assembly_ports=assembly_ports,
    )
