from __future__ import annotations

import pytest

from matmaster.context.assembly import ContextAssembler
from matmaster.context.compaction import CompactionPlan, ContextCompactor
from matmaster.context.ports import ContextAssemblyPorts, SessionEvent, UserInstructions
from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.context.sources.turn_input import TurnInput
from matmaster.types.message_normalization import normalize_and_validate_openai_messages
from matmaster.types.messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)
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


def make_compactor(*, boundary=lambda: 9) -> ContextCompactor:
    assembler = ContextAssembler(
        ContextAssemblyPorts(session_events=EventsPort()),
        _session_section_builder_for_tests=session_sections,
    )
    return ContextCompactor(
        config=CompactionConfig(context_limit=1000, trigger_ratio=0.9),
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

    result = await compactor.apply_summary(
        CompactionPlan(
            compaction_id="root:1",
            compaction_count=1,
            phase="runtime",
            trigger_tokens=999,
            turn=3,
        ),
        messages,
        "Summary text.",
    )

    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], UserMessage)
    assert result.base_snapshot is not None
    assert "<compacted_history>" in result.base_snapshot[0]["content"]
    assert "<previous_session_summary>" not in result.base_snapshot[0]["content"]
    assert result.strategy == "summary"
    assert result.durability == "durable"
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

    result = await compactor.apply_fallback(
        CompactionPlan(
            compaction_id="root:1",
            compaction_count=1,
            phase="runtime",
            trigger_tokens=999,
            turn=3,
        ),
        messages,
        failure_reason="runtime_current_event_boundary_missing",
    )

    assert result.durability == "ephemeral"
    assert result.base_snapshot is None
    assert result.failure_reason == "runtime_current_event_boundary_missing"


@pytest.mark.asyncio
async def test_preflight_compaction_uses_raw_current_input_without_double_wrap() -> (
    None
):
    compactor = make_compactor()
    ctx = TurnInput.from_values(
        user_text="Use current file.",
        files=["https://oss/current.cif"],
        pre_turn_history_event_id=7,
    )
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="old question"),
        AssistantMessage(content="old answer"),
        UserMessage(content="<user_instructions>wrapped</user_instructions>"),
    ]

    result = await compactor.apply_summary(
        compactor.plan_preflight_compaction(messages),
        messages,
        "Summary text.",
        turn_input=ctx,
    )

    runtime_content = messages[1].content or ""
    assert runtime_content.count("<current_instruction>") == 1
    assert "Use current file." in runtime_content
    assert "wrapped" not in runtime_content
    assert result.checkpoint_covered_until_event_id == 7


@pytest.mark.asyncio
async def test_apply_summary_replaces_messages_and_returns_durable_snapshot() -> None:
    compactor = make_compactor()
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="old question"),
        AssistantMessage(content="old answer"),
    ]
    plan = CompactionPlan(
        compaction_id="root:1",
        compaction_count=1,
        phase="runtime",
        trigger_tokens=999,
        turn=3,
    )

    result = await compactor.apply_summary(plan, messages, "Summary text.")

    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], UserMessage)
    assert "<compacted_history>" in (messages[1].content or "")
    assert result.strategy == "summary"
    assert result.durability == "durable"
    assert result.base_snapshot is not None
    assert result.checkpoint_covered_until_event_id == 9


@pytest.mark.asyncio
async def test_apply_fallback_selects_tool_safe_tail() -> None:
    compactor = make_compactor()
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="run"),
        AssistantMessage(
            content="",
            tool_calls=[
                ToolCallData(id="a", name="tool", arguments={}),
                ToolCallData(id="b", name="tool", arguments={}),
            ],
        ),
        ToolMessage(content="A", tool_call_id="a", tool_name="tool"),
        ToolMessage(content="B", tool_call_id="b", tool_name="tool"),
        AssistantMessage(content="done"),
    ]
    plan = CompactionPlan(
        compaction_id="root:1",
        compaction_count=1,
        phase="runtime",
        trigger_tokens=999,
        turn=3,
    )

    result = await compactor.apply_fallback(
        plan,
        messages,
        failure_reason="summary failed",
    )

    assert result.strategy == "sliding_window"
    assert result.durability == "ephemeral"
    assert result.failure_reason == "summary failed"
    assert result.base_snapshot is None
    normalize_and_validate_openai_messages(messages)


@pytest.mark.asyncio
async def test_apply_fallback_raises_and_does_not_mutate_all_orphan_tail() -> None:
    compactor = make_compactor()
    messages = [
        SystemMessage(content="sys"),
        ToolMessage(content="orphan", tool_call_id="missing", tool_name="tool"),
    ]
    original = list(messages)
    plan = CompactionPlan(
        compaction_id="root:1",
        compaction_count=1,
        phase="runtime",
        trigger_tokens=999,
        turn=3,
    )

    with pytest.raises(ValueError, match="runtime fallback produced empty tail"):
        await compactor.apply_fallback(plan, messages, failure_reason="summary failed")

    assert messages == original
