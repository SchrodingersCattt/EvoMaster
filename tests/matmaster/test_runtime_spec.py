from __future__ import annotations

import pytest

from matmaster.context.system_prompt import SystemPromptBuilder
from matmaster.types.runtime import AgentRuntimeSpec


def _ctx_builder() -> SystemPromptBuilder:
    return SystemPromptBuilder()


def test_spec_accepts_no_context_assembler_or_ports() -> None:
    spec = AgentRuntimeSpec(system_prompt_builder=_ctx_builder())
    assert spec.context_assembler is None
    assert spec.user_instructions_port is None
    assert spec.session_events_port is None
    assert spec.session_jobs_port is None


def test_spec_accepts_real_context_assembler() -> None:
    from matmaster.context.assembly import ContextAssembler
    from matmaster.context.ports import ContextAssemblyPorts

    class _StubEventsPort:
        async def load_events(self, _query):  # noqa: ARG002
            return ()

    assembler = ContextAssembler(
        ports=ContextAssemblyPorts(session_events=_StubEventsPort())
    )
    spec = AgentRuntimeSpec(
        system_prompt_builder=_ctx_builder(),
        context_assembler=assembler,
    )
    assert spec.context_assembler is assembler


def test_spec_rejects_non_assembler_type_for_context_assembler() -> None:
    with pytest.raises(ValueError, match="context_assembler"):
        AgentRuntimeSpec(
            system_prompt_builder=_ctx_builder(),
            context_assembler="not-an-assembler",
        )


def test_spec_accepts_real_user_instructions_port() -> None:
    from src.services.context_assembly_ports import AppUserInstructionsPort

    spec = AgentRuntimeSpec(
        system_prompt_builder=_ctx_builder(),
        user_instructions_port=AppUserInstructionsPort(),
    )
    assert isinstance(spec.user_instructions_port, AppUserInstructionsPort)


def test_spec_accepts_real_session_events_port() -> None:
    from src.services.context_assembly_ports import AppSessionEventsPort

    class _EventsTable:
        def query_context_events(self, **_kwargs):
            return []

    port = AppSessionEventsPort(events_table=_EventsTable())
    spec = AgentRuntimeSpec(
        system_prompt_builder=_ctx_builder(),
        session_events_port=port,
    )
    assert spec.session_events_port is port


def test_spec_accepts_optional_session_jobs_port_as_none() -> None:
    spec = AgentRuntimeSpec(
        system_prompt_builder=_ctx_builder(),
        session_jobs_port=None,
    )
    assert spec.session_jobs_port is None
