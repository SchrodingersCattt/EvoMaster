from __future__ import annotations

import pytest

from matmaster.context.assembly import (
    AssemblyResult,
    CompactionAssemblyRequest,
    ContextAssembler,
    ContextAssemblyIntent,
    ContextRenderOptions,
    TurnAssemblyRequest,
)
from matmaster.context.ports import (
    ActiveSkill,
    ContextAssemblyPorts,
    SessionEvent,
    SessionEventQuery,
    UserInstructions,
    WorkspaceJobs,
)
from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.context.session import SessionContextBuilder
from matmaster.context.sources.turn_input import (
    TurnAttachmentsSource,
    TurnInput,
    TurnInstructionSource,
)


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

    async def load_workspace_jobs(self, query):
        self.queries.append(query)
        return WorkspaceJobs(active_jobs=({"id": "job-1"},))


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
        ContextAssemblyPorts(session_events=events_port, workspace_jobs=jobs_port),
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
    assert (
        result.user_turn_context.render(ContextView.RUNTIME).count("<session_tools>")
        == 1
    )


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
async def test_assembler_render_options_default_to_merged_turn_attachments() -> None:
    assembler = ContextAssembler(ContextAssemblyPorts(session_events=EventsPort()))

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.CONTINUATION_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="Use current file."),
                attachments=TurnAttachmentsSource(files=("https://oss/a.cif",)),
                pre_turn_history_event_id=5,
            ),
            user_instructions=_instructions(),
        ),
    )

    runtime = result.user_turn_context.render(ContextView.RUNTIME)
    assert "<current_instruction>" in runtime
    assert "[Current attachments]" in runtime
    assert "<turn_attachments>" not in runtime


@pytest.mark.asyncio
async def test_assembler_render_options_can_split_turn_attachments() -> None:
    assembler = ContextAssembler(
        ContextAssemblyPorts(session_events=EventsPort()),
        render_options=ContextRenderOptions(split_turn_attachments=True),
    )

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.CONTINUATION_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="Use current file."),
                attachments=TurnAttachmentsSource(files=("https://oss/a.cif",)),
                pre_turn_history_event_id=5,
            ),
            user_instructions=_instructions(),
        ),
    )

    runtime = result.user_turn_context.render(ContextView.RUNTIME)
    assert "<current_instruction>" in runtime
    assert "<turn_attachments>" in runtime
    assert "[Current attachments]" not in runtime


@pytest.mark.asyncio
async def test_assemble_compaction_prefight_derives_covered_until_from_turn_input() -> (
    None
):
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
async def test_compaction_checkpoint_excludes_current_turn_images() -> None:
    assembler = ContextAssembler(
        ContextAssemblyPorts(session_events=EventsPort()),
        _session_section_builder_for_tests=_session_builder,
    )

    result = await assembler.assemble_compaction(
        ContextAssemblyIntent.PREFLIGHT_COMPACTION,
        CompactionAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            user_instructions=_instructions(),
            compacted_history_summary="Earlier turns mention FeO.",
            turn_input=TurnInput(
                attachments=TurnAttachmentsSource(
                    images=("https://example.com/current-turn.png",)
                ),
                pre_turn_history_event_id=12,
            ),
        ),
    )

    runtime = result.user_turn_context.to_message(ContextView.RUNTIME)
    checkpoint = result.user_turn_context.to_message(ContextView.CHECKPOINT)

    assert [image.url for image in runtime.images] == [
        "https://example.com/current-turn.png"
    ]
    assert checkpoint.images == []


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


class _RecordingEventsPort:
    def __init__(self, events: tuple[SessionEvent, ...]) -> None:
        self._events = events
        self.queries = []

    async def load_events(self, query):
        self.queries.append(query)
        return self._events


class _UnscopedEventsPort:
    """Test double that intentionally returns events past `until_event_id`."""

    def __init__(self, events: tuple[SessionEvent, ...]) -> None:
        self._events = events
        self.captured_query: SessionEventQuery | None = None

    async def load_events(self, query):
        self.captured_query = query
        return self._events


@pytest.mark.asyncio
async def test_assemble_turn_anchor_uses_session_context_factory() -> None:
    events = (
        SessionEvent(
            id=1,
            event_type="query",
            source="User",
            content={"files": ("https://oss.example.com/a.csv",)},
        ),
        SessionEvent(
            id=2,
            event_type="skill_hit",
            source="System",
            content={"skill_name": "pxrd"},
        ),
    )
    port = _RecordingEventsPort(events)

    def factory(loaded_events: tuple[SessionEvent, ...]) -> SessionContextBuilder:
        return SessionContextBuilder(
            events=loaded_events,
            active_skills=(
                ActiveSkill(
                    name="pxrd",
                    description="PXRD helper",
                    mcp_server="mat_xrd",
                ),
            ),
            legal_mcp_servers={"mat_xrd"},
            schemas_by_server={"mat_xrd": [{"name": "read"}]},
        )

    assembler = ContextAssembler(
        ContextAssemblyPorts(session_events=port),
        session_context_factory=factory,
    )

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="hi"),
                pre_turn_history_event_id=2,
            ),
            user_instructions=UserInstructions(text="Use SI.", hash="sha256:abc"),
        ),
    )

    runtime = result.user_turn_context.render(ContextView.RUNTIME)
    assert "<loaded_skills>" in runtime
    assert "<active_tools>" in runtime
    assert "<attachments>" in runtime
    assert port.queries[0].until_event_id == 2


@pytest.mark.asyncio
async def test_assemble_turn_continuation_does_not_invoke_session_factory() -> None:
    call_count = 0

    def factory(_events):
        nonlocal call_count
        call_count += 1
        return SessionContextBuilder(
            events=(),
            active_skills=(),
            legal_mcp_servers=None,
            schemas_by_server=None,
        )

    assembler = ContextAssembler(
        ContextAssemblyPorts(session_events=_RecordingEventsPort(())),
        session_context_factory=factory,
    )

    await assembler.assemble_turn(
        ContextAssemblyIntent.CONTINUATION_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="continue"),
                pre_turn_history_event_id=2,
            ),
            user_instructions=UserInstructions(text="Use SI.", hash="sha256:abc"),
        ),
    )

    assert call_count == 0


@pytest.mark.asyncio
async def test_assembler_scopes_events_to_until_event_id_before_factory() -> None:
    events = (
        SessionEvent(
            id=1,
            event_type="skill_hit",
            source=None,
            content={"skill_name": "pxrd"},
        ),
        SessionEvent(
            id=9,
            event_type="skill_hit",
            source=None,
            content={"skill_name": "mlip"},
        ),
    )

    seen_event_ids: list[tuple[int, ...]] = []

    def resolver(scoped_events: tuple[SessionEvent, ...]) -> tuple[ActiveSkill, ...]:
        seen_event_ids.append(tuple(event.id for event in scoped_events))
        return tuple(
            ActiveSkill(name=str(event.content["skill_name"]))
            for event in scoped_events
            if event.event_type == "skill_hit"
        )

    def factory(scoped_events: tuple[SessionEvent, ...]) -> SessionContextBuilder:
        return SessionContextBuilder(
            events=scoped_events,
            active_skills=resolver(scoped_events),
        )

    assembler = ContextAssembler(
        ports=ContextAssemblyPorts(
            session_events=_UnscopedEventsPort(events),
            workspace_jobs=None,
        ),
        session_context_factory=factory,
    )

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput.from_values(
                user_text="",
                pre_turn_history_event_id=1,
            ),
            user_instructions=UserInstructions(text="", hash=""),
        ),
    )

    assert seen_event_ids == [(1,)]
    rendered = result.user_turn_context.render(ContextView.RUNTIME)
    assert "pxrd" in rendered
    assert "mlip" not in rendered


@pytest.mark.asyncio
async def test_assembler_default_session_factory_returns_empty_sections() -> None:
    port = _RecordingEventsPort(())
    assembler = ContextAssembler(ContextAssemblyPorts(session_events=port))

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="hi"),
                pre_turn_history_event_id=0,
            ),
            user_instructions=UserInstructions(text="Use SI.", hash="sha256:abc"),
        ),
    )

    runtime = result.user_turn_context.render(ContextView.RUNTIME)
    assert "<loaded_skills>" not in runtime
    assert "<active_tools>" not in runtime
