"""Runtime context assembly wiring for Exp.build_runtime().

The assembly internals (assembler, ports, frozen user instructions, covered
boundary provider) are collected into ``ContextAssemblyRuntime`` and consumed
by ``ContextCompactor``. ``RuntimeContextAssembly`` exposes only the compactor
and the context_runtime to ``Exp.build_runtime()``; the kernel-facing runtime
never sees these objects -- it reaches context reassembly solely through
``AgentKernelResources.compactor``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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
)
from matmaster.context.session import SessionContextBuilder
from matmaster.core.run_context import AgentRunContext
from matmaster.types.runtime import CompactionConfig
from matmaster.types.runtime_ports import EmptySessionEventHistory

SessionContextFactory = Callable[[tuple[SessionEvent, ...]], SessionContextBuilder]


@dataclass(frozen=True)
class ContextAssemblyRuntime:
    """Runtime context reassembly capability owned by the compactor.

    Holds the assembler, its ports, the frozen per-run user instructions, and
    the covered-boundary provider. Not exposed on the kernel-facing runtime.
    """

    assembler: ContextAssembler
    ports: ContextAssemblyPorts
    user_instructions: UserInstructions
    covered_until_provider: Callable[[], int | None]


@dataclass(frozen=True)
class RuntimeContextAssembly:
    context_runtime: ContextAssemblyRuntime | None = None
    compactor: ContextCompactor | None = None


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
    llm_provider: Any,
    compaction: CompactionConfig,
    ctx: AgentRunContext,
    skill_resolver: SkillResolver,
    spawn_id: str | None,
    logger: logging.Logger,
) -> RuntimeContextAssembly:
    """Build context assembler and compactor resources for runtime execution."""
    if llm_provider is None:
        return RuntimeContextAssembly()

    history_port = ctx.request.ports.compaction.history
    if history_port is None:
        history_port = EmptySessionEventHistory()

    user_instructions = ctx.request.user_instructions or UserInstructions.empty()
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
    context_runtime = ContextAssemblyRuntime(
        assembler=context_assembler,
        ports=assembly_ports,
        user_instructions=user_instructions,
        covered_until_provider=history_port.latest_scope_event_id,
    )

    compactor = ContextCompactor(
        config=compaction,
        context_assembler=context_runtime.assembler,
        user_instructions=context_runtime.user_instructions,
        session_id=ctx.environment.session_id,
        spawn_id=spawn_id,
        runtime_covered_until_provider=context_runtime.covered_until_provider,
        event_sink=None,
        compaction_scope=(f'{ctx.environment.metadata.task_id}:{spawn_id or "root"}'),
    )

    return RuntimeContextAssembly(
        context_runtime=context_runtime,
        compactor=compactor,
    )
