from __future__ import annotations

import pytest

from matmaster.context.assembly import ContextAssembler
from matmaster.context.compaction import CompactionPlan, ContextCompactor
from matmaster.context.ports import ContextAssemblyPorts, SessionEvent, UserInstructions
from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.types.current_input import CurrentInputContext
from matmaster.types.messages import AssistantMessage, LLMResponse, SystemMessage, UserMessage
from matmaster.types.runtime import CompactionConfig


class EventsPort:
    def __init__(self) -> None:
        self.queries = []

    async def load_events(self, query):
        self.queries.append(query)
        return (
            SessionEvent(
                id=1,
                event_type="query",
                source="User",
                content={"content": "old question"},
            ),
        )


def session_sections(events, until_event_id, include_attachments):
    sections = []
    if include_attachments:
        sections.append(
            ContextSection(
                key="session_attachments",
                tag="session_attachments",
                content="file_1 old.cif https://oss/old.cif",
                order=SectionOrder.SESSION_ATTACHMENTS,
                views=frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT}),
            )
        )
    return tuple(sections)


class Provider:
    def __init__(self, summary: str = "Summary text.") -> None:
        self.summary = summary
        self.calls = []

    async def chat(self, messages, tools=None):
        self.calls.append(messages)
        return LLMResponse(content=self.summary, finish_reason="stop")


def make_compactor(*, provider=None, boundary=lambda: 9) -> ContextCompactor:
    assembler = ContextAssembler(
        ContextAssemblyPorts(session_events=EventsPort()),
        _session_section_builder_for_tests=session_sections,
    )
    return ContextCompactor(
        config=CompactionConfig(context_limit=1000, trigger_ratio=0.9),
        summary_provider=provider or Provider(),
        context_assembler=assembler,
        user_instructions=UserInstructions(text="Use SI units.", hash="sha256:abc"),
        session_id="sess-1",
        spawn_id=None,
        runtime_covered_until_provider=boundary,
    )


@pytest.mark.asyncio
async def test_runtime_compaction_uses_high_water_and_compacted_history_marker() -> (
    None
):
    compactor = make_compactor()
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="old question"),
        AssistantMessage(content="old answer"),
    ]

    result = await compactor.apply_compaction_plan(
        CompactionPlan(
            compaction_id="root:1",
            compaction_count=1,
            phase="runtime",
            trigger_tokens=999,
            turn=3,
        ),
        messages,
    )

    assert result.base_snapshot is not None
    assert "<compacted_history>" in result.base_snapshot[0]["content"]
    assert "<previous_session_summary>" not in result.base_snapshot[0]["content"]
    assert result.checkpoint_covered_until_event_id == 9
    assert result.user_instructions_text == "Use SI units."
    assert result.user_instructions_hash == "sha256:abc"


@pytest.mark.asyncio
async def test_runtime_compaction_missing_boundary_uses_fallback() -> None:
    compactor = make_compactor(boundary=lambda: None)
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="old question"),
        AssistantMessage(content="old answer"),
    ]

    result = await compactor.apply_compaction_plan(
        CompactionPlan(
            compaction_id="root:1",
            compaction_count=1,
            phase="runtime",
            trigger_tokens=999,
            turn=3,
        ),
        messages,
    )

    assert result.durability == "ephemeral"
    assert result.base_snapshot is None
    assert result.failure_reason == "runtime_current_event_boundary_missing"


@pytest.mark.asyncio
async def test_preflight_compaction_uses_raw_current_input_without_double_wrap() -> (
    None
):
    compactor = make_compactor()
    ctx = CurrentInputContext.from_values(
        user_text="Use current file.",
        files=["https://oss/current.cif"],
        pre_query_scope_event_id=7,
    )
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="old question"),
        AssistantMessage(content="old answer"),
        UserMessage(content="<user_instructions>wrapped</user_instructions>"),
    ]

    result = await compactor.apply_compaction_plan(
        compactor.plan_preflight_compaction(messages),
        messages,
        current_input_context=ctx,
    )

    runtime_content = messages[1].content or ""
    assert runtime_content.count("<current_instruction>") == 1
    assert "Use current file." in runtime_content
    assert "wrapped" not in runtime_content
    assert result.checkpoint_covered_until_event_id == 7
