"""Runtime context assembly wiring for Exp.build_runtime()."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from matmaster.context.assembly import ContextAssembler, ContextRenderOptions
from matmaster.context.compaction import ContextCompactor
from matmaster.context.ports import (
    ContextAssemblyPorts,
    SessionEvent,
    SessionEventQuery,
    SessionJobs,
    SessionJobsQuery,
    UserInstructions,
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


def _hash_user_instructions(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_session_context_factory(
    *,
    skill_registry: Any | None,
    legal_mcp_servers: set[str] | None,
    schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None,
) -> SessionContextFactory:
    def factory(events: tuple[SessionEvent, ...]) -> SessionContextBuilder:
        return SessionContextBuilder(
            events=events,
            skill_registry=skill_registry,
            legal_mcp_servers=legal_mcp_servers,
            schemas_by_server=schemas_by_server,
        )

    return factory


class _EmptySessionJobsPort:
    async def load_session_jobs(self, query: SessionJobsQuery) -> SessionJobs:
        return SessionJobs.empty()


def build_runtime_context_assembly(
    *,
    spec: AgentRuntimeSpec,
    ctx: PlaygroundContext,
    skill_registry: Any,
    spawn_id: str | None,
    logger: logging.Logger,
) -> RuntimeContextAssembly:
    """Build context assembler and compactor resources for runtime execution."""
    if spec.llm_provider is None:
        return RuntimeContextAssembly()

    run_meta = getattr(ctx, "run_meta", {}) or {}
    summary_provider = spec.llm_provider
    if spec.compaction.compaction_llm:
        llm_config = getattr(ctx, "llm_config", None)
        if llm_config is not None:
            from matmaster.providers.llm_factory import build_provider

            try:
                summary_provider = build_provider(
                    llm_config,
                    llm_override=spec.compaction.compaction_llm,
                )
            except KeyError:
                logger.warning(
                    "compaction_llm key=%r not found, falling back to main provider",
                    spec.compaction.compaction_llm,
                )
        else:
            logger.warning(
                "compaction_llm key=%r set but no llm_config on context; "
                "falling back to main provider",
                spec.compaction.compaction_llm,
            )

    history_port = ctx.runtime_ports.compaction.history
    if history_port is None:
        history_port = EmptySessionEventHistory()

    instructions_text = str(run_meta.get("user_instructions") or "")
    instructions_hash = run_meta.get("user_instructions_hash")
    if not isinstance(instructions_hash, str) or not instructions_hash:
        instructions_hash = _hash_user_instructions(instructions_text)
    user_instructions = UserInstructions(
        text=instructions_text,
        hash=instructions_hash,
        truncated=bool(run_meta.get("user_instructions_truncated", False)),
    )
    assembly_ports = ContextAssemblyPorts(
        session_events=history_port,
        session_jobs=_EmptySessionJobsPort(),
    )
    context_assembler = ContextAssembler(
        ports=assembly_ports,
        session_context_factory=build_session_context_factory(
            skill_registry=skill_registry,
            legal_mcp_servers=run_meta.get("legal_mcp_servers"),
            schemas_by_server=run_meta.get("schemas_by_server"),
        ),
        render_options=ContextRenderOptions(
            split_turn_attachments=bool(run_meta.get("split_turn_attachments", False)),
        ),
    )

    return RuntimeContextAssembly(
        compactor=ContextCompactor(
            config=spec.compaction,
            summary_provider=summary_provider,
            context_assembler=context_assembler,
            user_instructions=user_instructions,
            session_id=run_meta.get("session_id") or "",
            spawn_id=spawn_id,
            runtime_covered_until_provider=history_port.latest_scope_event_id,
            event_sink=None,
            compaction_scope=f'{run_meta.get("task_id", "")}:{spawn_id or "root"}',
        ),
        context_assembler=context_assembler,
        assembly_ports=assembly_ports,
    )
