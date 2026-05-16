from __future__ import annotations

import pytest

from matmaster.context.assembly import (
    AssemblyResult,
    CompactionAssemblyRequest,
    ContextAssembler,
    ContextAssemblyIntent,
    TurnAssemblyRequest,
)
from matmaster.context.ports import (
    ContextAssemblyPorts,
    SessionEvent,
    SessionJobs,
    UserInstructions,
)
from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.context.sources.turn_input import TurnInput, TurnInstructionSource


class EventsPort:
    def __init__(self) -> None:
        self.queries = []

    async def load_events(self, query):
        self.queries.append(query)
        return (
            SessionEvent(
                id=1,
                event_type="skill_hit",
                source="System",
                content={"name": "vasp"},
            ),
        )


class JobsPort:
    def __init__(self) -> None:
        self.queries = []

    async def load_session_jobs(self, query):
        self.queries.append(query)
        return SessionJobs(active_jobs=({"id": "job-1"},))


def _session_builder(events, until_event_id, include_attachments):
    assert events[0].id == 1
    assert until_event_id == 12
    assert include_attachments is True
    return (
        ContextSection(
            key="session_tools",
            tag="session_tools",
            content="VASP",
            order=SectionOrder.SESSION_TOOLS,
            views=frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT}),
        ),
    )


def _instructions() -> UserInstructions:
    return UserInstructions(text="Use SI units.", hash="sha256:abc")


@pytest.mark.asyncio
async def test_assemble_turn_anchor_loads_events_and_jobs() -> None:
    events_port = EventsPort()
    jobs_port = JobsPort()
    assembler = ContextAssembler(
        ContextAssemblyPorts(session_events=events_port, session_jobs=jobs_port),
        _session_section_builder_for_tests=_session_builder,
    )

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="Explain FeO."),
                pre_turn_history_event_id=12,
            ),
            user_instructions=_instructions(),
        ),
    )

    assert isinstance(result, AssemblyResult)
    assert result.user_instructions_text == "Use SI units."
    assert result.user_instructions_hash == "sha256:abc"
    assert result.used_composition == "anchor"
    assert result.covered_until_event_id is None
    assert len(events_port.queries) == 1
    assert events_port.queries[0].until_event_id == 12
    assert len(jobs_port.queries) == 1
    assert result.user_turn_context.render(ContextView.RUNTIME).count(
        "<session_tools>"
    ) == 1


@pytest.mark.asyncio
async def test_assemble_turn_continuation_does_not_load_events() -> None:
    events_port = EventsPort()
    assembler = ContextAssembler(ContextAssemblyPorts(session_events=events_port))

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.CONTINUATION_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="Continue."),
                pre_turn_history_event_id=99,
            ),
            user_instructions=_instructions(),
        ),
    )

    assert events_port.queries == []
    assert result.used_composition == "continuation"
    assert "<user_instructions>" not in result.user_turn_context.render(
        ContextView.RUNTIME
    )


@pytest.mark.asyncio
async def test_assemble_compaction_prefight_derives_covered_until_from_turn_input() -> None:
    events_port = EventsPort()
    assembler = ContextAssembler(
        ContextAssemblyPorts(session_events=events_port),
        _session_section_builder_for_tests=_session_builder,
    )

    result = await assembler.assemble_compaction(
        ContextAssemblyIntent.PREFLIGHT_COMPACTION,
        CompactionAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            user_instructions=_instructions(),
            compacted_history_summary="Earlier turns mention FeO.",
            turn_input=TurnInput(pre_turn_history_event_id=12),
        ),
    )

    assert result.covered_until_event_id == 12
    runtime = result.user_turn_context.to_message(ContextView.RUNTIME)
    checkpoint = result.user_turn_context.to_message(ContextView.CHECKPOINT)
    assert "<compacted_history>" in runtime.content
    assert "<current_instruction>" not in checkpoint.content


@pytest.mark.asyncio
async def test_assemble_compaction_runtime_requires_explicit_boundary() -> None:
    assembler = ContextAssembler(ContextAssemblyPorts(session_events=EventsPort()))

    with pytest.raises(ValueError, match="RUNTIME_COMPACTION requires explicit"):
        await assembler.assemble_compaction(
            ContextAssemblyIntent.RUNTIME_COMPACTION,
            CompactionAssemblyRequest(
                session_id="sess-1",
                spawn_id=None,
                user_instructions=_instructions(),
                compacted_history_summary="Earlier turns mention FeO.",
            ),
        )


@pytest.mark.asyncio
async def test_assemble_compaction_rejects_wrong_intent() -> None:
    assembler = ContextAssembler(ContextAssemblyPorts(session_events=EventsPort()))

    with pytest.raises(ValueError, match="assemble_compaction does not accept"):
        await assembler.assemble_compaction(
            ContextAssemblyIntent.ANCHOR_TURN,
            CompactionAssemblyRequest(
                session_id="sess-1",
                spawn_id=None,
                user_instructions=_instructions(),
                compacted_history_summary="Earlier turns mention FeO.",
                covered_until_event_id=1,
            ),
        )
