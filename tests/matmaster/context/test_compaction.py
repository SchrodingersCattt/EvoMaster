from __future__ import annotations

import pytest

from matmaster.context.assembly import ContextAssembler
from matmaster.context.compaction import (
    CompactionPlan,
    ContextCompactor,
    prepare_messages_for_summary_call,
)
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
    assert result.base_messages is not None
    assert "<compacted_history>" in result.base_messages[0]["content"]
    assert "<previous_session_summary>" not in result.base_messages[0]["content"]
    assert result.strategy == "summary"
    assert result.durability == "durable"
    assert result.checkpoint_covered_until_event_id == 9
    assert result.user_instructions_text == "Use SI units."
    assert result.user_instructions_hash == "sha256:abc"


@pytest.mark.asyncio
async def test_runtime_compaction_reinjects_current_instruction_text() -> None:
    compactor = make_compactor()
    turn_input = TurnInput.from_values(
        user_text="Run exact fitting with alpha=0.37.",
        files=["https://oss.example.com/current.cif"],
        images=["https://oss.example.com/current.png"],
        image_detail="high",
        workspace_paths=["/share/current/POSCAR"],
        pre_turn_history_event_id=3,
    )
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="Run exact fitting with alpha=0.37."),
        AssistantMessage(content="working"),
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
        "Summary only mentions previous context.",
        turn_input=turn_input,
    )

    runtime_content = messages[1].content or ""
    assert "<compacted_history>" in runtime_content
    assert (
        "<current_instruction>\n"
        "Run exact fitting with alpha=0.37.\n"
        "</current_instruction>"
    ) in runtime_content
    assert "current.cif" not in runtime_content
    assert "current.png" not in runtime_content
    assert "/share/current/POSCAR" not in runtime_content
    assert messages[1].images == []
    assert result.base_messages is not None
    assert "<current_instruction>" not in result.base_messages[0]["content"]
    assert result.checkpoint_covered_until_event_id == 9


@pytest.mark.asyncio
async def test_runtime_compaction_keeps_omitted_current_request_authoritative() -> None:
    compactor = make_compactor()
    turn_input = TurnInput.from_values(
        user_text="Do not relax the cell; only compute static energy.",
        pre_turn_history_event_id=3,
    )
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="Do not relax the cell; only compute static energy."),
        AssistantMessage(content="starting calculation"),
    ]

    await compactor.apply_summary(
        CompactionPlan(
            compaction_id="root:1",
            compaction_count=1,
            phase="runtime",
            trigger_tokens=999,
            turn=3,
        ),
        messages,
        "Previous context says the user asked about FeO.",
        turn_input=turn_input,
    )

    runtime_content = messages[1].content or ""
    assert "Previous context says the user asked about FeO." in runtime_content
    assert "Do not relax the cell; only compute static energy." in runtime_content


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
    assert result.base_messages is None
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
    assert "file_1 current.cif https://oss/current.cif" in runtime_content
    assert "wrapped" not in runtime_content
    assert result.checkpoint_covered_until_event_id == 7


@pytest.mark.asyncio
async def test_preflight_plan_without_current_split_keeps_runtime_boundary() -> None:
    compactor = make_compactor(boundary=lambda: 33)
    turn_input = TurnInput.from_values(
        user_text="current query",
        pre_turn_history_event_id=7,
    )
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="current query"),
    ]

    result = await compactor.apply_summary(
        CompactionPlan(
            compaction_id="root:1",
            compaction_count=1,
            phase="preflight",
            trigger_tokens=999,
            turn=0,
        ),
        messages,
        "Summary text.",
        turn_input=turn_input,
    )

    runtime_content = messages[1].content or ""
    assert "<current_instruction>" not in runtime_content
    assert result.checkpoint_covered_until_event_id == 33


@pytest.mark.asyncio
async def test_preflight_summary_split_and_reattach_share_current_input_source() -> (
    None
):
    compactor = make_compactor()
    turn_input = TurnInput.from_values(
        user_text="current query",
        pre_turn_history_event_id=7,
    )
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="old question"),
        AssistantMessage(content="old answer"),
        UserMessage(
            content=(
                "<user_instructions>rendered instructions</user_instructions>\n"
                "<current_instruction>rendered current query</current_instruction>"
            )
        ),
    ]

    prep = prepare_messages_for_summary_call(
        full_messages=messages,
        phase="preflight",
        turn_input=turn_input,
        compact_request=UserMessage(content="Summarize history."),
        context_limit=10_000,
        reserved_summary_tokens=1_000,
        safety_margin_tokens=100,
    )

    assert prep.messages == messages[:-1]
    assert all(
        "rendered current query" not in (message.content or "")
        for message in prep.messages
    )

    result = await compactor.apply_summary(
        compactor.plan_preflight_compaction(messages),
        messages,
        "Summary text.",
        turn_input=turn_input,
    )

    runtime_content = messages[1].content or ""
    assert result.checkpoint_covered_until_event_id == 7
    assert runtime_content.count("<current_instruction>") == 1
    assert "current query" in runtime_content
    assert "rendered current query" not in runtime_content


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
    assert result.base_messages is not None
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
    assert result.base_messages is None
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
