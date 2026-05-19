"""Service-layer factory for ContextAssembler.

Binds platform-specific dependencies (events_table, skill resolver) to a
ContextAssembler instance. The session-context
factory is owned by ``matmaster.core.runtime_context_assembly`` so the
runtime and service paths share a single definition.
"""

from __future__ import annotations

from matmaster.context.assembly import ContextAssembler, ContextRenderOptions
from matmaster.context.ports import ContextAssemblyPorts, SkillResolver
from matmaster.core.runtime_context_assembly import build_session_context_factory
from src.services.context_assembly_ports import AppSessionEventsPort, AppSessionJobsPort


def build_context_assembler(
    *,
    events_table: object,
    skill_resolver: SkillResolver,
) -> tuple[ContextAssembler, ContextAssemblyPorts]:
    ports = ContextAssemblyPorts(
        session_events=AppSessionEventsPort(events_table=events_table),
        session_jobs=AppSessionJobsPort(),
    )
    assembler = ContextAssembler(
        ports=ports,
        session_context_factory=build_session_context_factory(
            skill_resolver=skill_resolver,
        ),
        render_options=ContextRenderOptions(),
    )
    return assembler, ports
