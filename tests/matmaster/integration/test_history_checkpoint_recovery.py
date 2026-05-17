from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from matmaster.context.assembly import ContextAssembler
from matmaster.context.compaction import CompactionPlan, ContextCompactor
from matmaster.context.ports import ContextAssemblyPorts, UserInstructions
from matmaster.core.runtime_context_assembly import build_session_context_factory
from matmaster.skills.registry import SkillRegistry
from matmaster.types.messages import (
    AssistantMessage,
    LLMResponse,
    StreamChunk,
    SystemMessage,
    UserMessage,
)
from matmaster.types.runtime import CompactionConfig
from src.services.context_assembly_ports import AppSessionEventsPort, AppSessionJobsPort
from src.services.history_checkpoint_codec import serialize_base_messages
from src.services.history_checkpoint_service import HistoryCheckpointService
from src.services.model_history_restore_service import ModelHistoryRestoreService


def _compact_user_message(summary: str) -> UserMessage:
    return UserMessage(content=f"<compacted_history>\n{summary}\n</compacted_history>")


class _SummaryProvider:
    def __init__(self, summary: str) -> None:
        self.summary = summary
        self.calls: list[list[dict[str, Any]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def chat(self, messages, tools=None, *, tool_choice=None):
        self.calls.append(messages)
        return LLMResponse(content=self.summary, finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content=self.summary, finish_reason="stop")


def _make_compactor_for_table(
    *,
    events_table: InMemoryEventsTable,
    session_id: str,
    tmp_path,
    provider: _SummaryProvider,
) -> ContextCompactor:
    assembler = ContextAssembler(
        ports=ContextAssemblyPorts(
            session_events=AppSessionEventsPort(events_table=events_table),
            session_jobs=AppSessionJobsPort(),
        ),
        session_context_factory=build_session_context_factory(
            skill_registry=SkillRegistry([tmp_path / "skills"]),
            legal_mcp_servers=None,
            schemas_by_server=None,
        ),
    )
    return ContextCompactor(
        config=CompactionConfig(context_limit=128000),
        context_assembler=assembler,
        user_instructions=UserInstructions(text="Use SI units.", hash="sha256:abc"),
        session_id=session_id,
        spawn_id=None,
        runtime_covered_until_provider=lambda: events_table.get_latest_scope_event_id(
            session_id,
            None,
        ),
    )


def _user_event(
    content: str,
    *,
    task_id: str | None = None,
    spawn_id: str | None = None,
) -> dict[str, Any]:
    return {
        "source": "User",
        "type": "query",
        "content": content,
        "task_id": task_id,
        "spawn_id": spawn_id,
    }


def _response_event(
    content: str,
    *,
    task_id: str | None = None,
    spawn_id: str | None = None,
) -> dict[str, Any]:
    return {
        "source": "MatMaster",
        "type": "response",
        "content": content,
        "task_id": task_id,
        "spawn_id": spawn_id,
    }


def _user_turn_context_event(content: str) -> dict[str, Any]:
    return {
        "source": "MatMaster",
        "type": "user_turn_context",
        "content": {
            "schema_version": "user_turn_context.v1",
            "kind": "anchor",
            "message": UserMessage(content=content).model_dump(mode="json"),
            "user_instructions_hash": "sha256:abc",
            "transform": "raw",
            "render_version": "user_context_render.v1",
        },
        "task_id": None,
        "spawn_id": None,
    }


class InMemoryEventsTable:
    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._next_id = 1
        self.calls: list[tuple[Any, ...]] = []

    def add_event(
        self,
        session_id: str,
        source: str,
        event_type: str,
        content: Any,
        *,
        task_id: str | None = None,
        invocation_id: str | None = None,
        spawn_id: str | None = None,
    ) -> int:
        event = {
            "id": self._next_id,
            "session_id": session_id,
            "source": source,
            "type": event_type,
            "content": content,
            "task_id": task_id,
            "invocation_id": invocation_id,
            "spawn_id": spawn_id,
        }
        self._next_id += 1
        self._events.append(event)
        return event["id"]

    def add_history_checkpoint(
        self,
        session_id: str,
        *,
        task_id: str | None,
        invocation_id: str | None,
        spawn_id: str | None,
        covered_until_event_id: int,
        base_messages: list[dict[str, Any]],
        reason: str = "summary",
        schema_version: str | None = None,
        render_version: str | None = None,
        user_instructions_text: str | None = None,
        user_instructions_hash: str | None = None,
    ) -> bool:
        self.calls.append(
            (
                "add_history_checkpoint",
                session_id,
                task_id,
                invocation_id,
                spawn_id,
                covered_until_event_id,
                reason,
            )
        )
        content = {
            "covered_until_event_id": covered_until_event_id,
            "base_messages": base_messages,
            "reason": reason,
        }
        if schema_version is not None:
            content["schema_version"] = schema_version
        if render_version is not None:
            content["render_version"] = render_version
        if user_instructions_text is not None:
            content["user_instructions_text"] = user_instructions_text
        if user_instructions_hash is not None:
            content["user_instructions_hash"] = user_instructions_hash

        self.add_event(
            session_id,
            "System",
            "history_checkpoint",
            content,
            task_id=task_id,
            invocation_id=invocation_id,
            spawn_id=spawn_id,
        )
        return True

    def get_history_checkpoints(
        self,
        session_id: str,
        spawn_id: str | None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        self.calls.append(("get_history_checkpoints", session_id, spawn_id, limit))
        rows = [
            event
            for event in self._events
            if event["session_id"] == session_id
            and event["type"] == "history_checkpoint"
            and event.get("spawn_id") == spawn_id
        ]
        rows.sort(key=lambda event: int(event["id"]), reverse=True)
        return rows[:limit]

    def get_scope_events_after_id(
        self,
        session_id: str,
        spawn_id: str | None,
        after_id: int | None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            ("get_scope_events_after_id", session_id, spawn_id, after_id, limit)
        )
        rows = [
            event
            for event in self._events
            if event["session_id"] == session_id
            and event.get("spawn_id") == spawn_id
            and (after_id is None or int(event["id"]) > after_id)
            and event["type"]
            not in {"history_checkpoint", "compaction", "context_compaction"}
        ]
        rows.sort(key=lambda event: int(event["id"]))
        if limit is not None:
            return rows[:limit]
        return rows

    def get_latest_scope_event_id(self, session_id: str, spawn_id: str | None) -> int:
        self.calls.append(("get_latest_scope_event_id", session_id, spawn_id))
        ids = [
            int(event["id"])
            for event in self._events
            if event["session_id"] == session_id
            and event.get("spawn_id") == spawn_id
            and event["type"]
            not in {"history_checkpoint", "compaction", "context_compaction"}
        ]
        return max(ids, default=0)

    def query_context_events(
        self,
        *,
        session_id: str,
        spawn_id: str | None,
        until_event_id: int | None = None,
        event_types: tuple[str, ...] | None = None,
        limit: int | None = None,
        order: str = "asc",
    ) -> list[dict[str, Any]]:
        self.calls.append(
            (
                "query_context_events",
                session_id,
                spawn_id,
                until_event_id,
                event_types,
                limit,
                order,
            )
        )
        rows = [
            event
            for event in self._events
            if event["session_id"] == session_id
            and event.get("spawn_id") == spawn_id
            and (until_event_id is None or int(event["id"]) <= until_event_id)
            and (not event_types or event["type"] in event_types)
        ]
        rows.sort(key=lambda event: int(event["id"]), reverse=order == "desc")
        if limit is not None:
            return rows[:limit]
        return rows

    def get_session_events(
        self,
        session_id: str,
        limit: int | None = None,
        include_spawn: bool = False,
    ) -> list[dict[str, Any]]:
        self.calls.append(("get_session_events", session_id, limit, include_spawn))
        rows = [
            event
            for event in self._events
            if event["session_id"] == session_id
            and (include_spawn or event.get("spawn_id") is None)
        ]
        rows.sort(key=lambda event: int(event["id"]))
        if limit is not None:
            return rows[:limit]
        return rows

    def has_user_turn_context(
        self,
        session_id: str,
        spawn_id: str | None,
    ) -> bool:
        self.calls.append(("has_user_turn_context", session_id, spawn_id))
        return any(
            event["session_id"] == session_id
            and event.get("spawn_id") == spawn_id
            and event["type"] == "user_turn_context"
            for event in self._events
        )

    def history_checkpoints(self, *, spawn_id: str | None) -> list[dict[str, Any]]:
        return [
            event
            for event in self._events
            if event["type"] == "history_checkpoint"
            and event.get("spawn_id") == spawn_id
        ]

    def compact_boundaries(self, *, spawn_id: str | None) -> list[dict[str, Any]]:
        return [
            event
            for event in self._events
            if event["type"] == "compact_boundary" and event.get("spawn_id") == spawn_id
        ]


def _seed_scope_events(
    events_table: InMemoryEventsTable,
    *,
    session_id: str,
    spawn_id: str | None = None,
    task_id: str | None = None,
    user_content: str,
    response_content: str,
    write_user_turn_context: bool = False,
) -> None:
    user_event = _user_event(user_content, task_id=task_id, spawn_id=spawn_id)
    events_table.add_event(
        session_id,
        user_event["source"],
        user_event["type"],
        user_event["content"],
        task_id=task_id,
        spawn_id=spawn_id,
    )
    if write_user_turn_context:
        utc_event = _user_turn_context_event(user_content)
        events_table.add_event(
            session_id,
            utc_event["source"],
            utc_event["type"],
            utc_event["content"],
            task_id=task_id,
            invocation_id=f"{task_id or 'task'}:utc",
            spawn_id=spawn_id,
        )
    response_event = _response_event(
        response_content, task_id=task_id, spawn_id=spawn_id
    )
    events_table.add_event(
        session_id,
        response_event["source"],
        response_event["type"],
        response_event["content"],
        task_id=task_id,
        spawn_id=spawn_id,
    )


def test_restore_v1_roundtrip_from_user_turn_context_event() -> None:
    table = InMemoryEventsTable()
    table.add_event(
        "sess-v1",
        "MatMaster",
        "user_turn_context",
        {
            "schema_version": "user_turn_context.v1",
            "kind": "anchor",
            "message": UserMessage(content="provider-facing question").model_dump(
                mode="json"
            ),
            "user_instructions_hash": "sha256:abc",
            "transform": "raw",
            "render_version": "user_context_render.v1",
        },
        task_id="task-1",
        invocation_id="inv-1",
        spawn_id=None,
    )
    table.add_event(
        "sess-v1",
        "MatMaster",
        "response",
        {"content": "answer"},
        task_id="task-1",
        invocation_id="inv-1",
        spawn_id=None,
    )

    history = ModelHistoryRestoreService(table).restore_history(
        session_id="sess-v1",
        spawn_id=None,
        task_id=None,
    )

    assert [message.role for message in history] == ["user", "assistant"]
    assert history[0].content == "provider-facing question"


def test_restore_v1_dedup_keeps_single_user_message_on_worker_retry() -> None:
    table = InMemoryEventsTable()
    table.add_event(
        "sess-dup",
        "MatMaster",
        "user_turn_context",
        {
            "schema_version": "user_turn_context.v1",
            "kind": "anchor",
            "message": UserMessage(content="single question").model_dump(mode="json"),
            "user_instructions_hash": "sha256:abc",
            "transform": "raw",
            "render_version": "user_context_render.v1",
        },
        task_id="task-1",
        invocation_id="inv-1",
        spawn_id=None,
    )
    table.add_event(
        "sess-dup",
        "MatMaster",
        "response",
        {"content": "single answer"},
        task_id="task-1",
        invocation_id="inv-1",
        spawn_id=None,
    )

    history = ModelHistoryRestoreService(table).restore_history(
        session_id="sess-dup",
        spawn_id=None,
        task_id=None,
    )

    user_messages = [m for m in history if isinstance(m, UserMessage)]
    assert len(user_messages) == 1
    assert user_messages[0].content == "single question"


def test_restore_v1_hybrid_mixed_session_preserves_pre_phase1_user_query() -> None:
    table = InMemoryEventsTable()
    table.add_event(
        "sess-mix",
        "User",
        "query",
        {"content": "old raw question"},
        task_id="old-task",
        invocation_id="inv-old",
        spawn_id=None,
    )
    table.add_event(
        "sess-mix",
        "MatMaster",
        "response",
        {"content": "old answer"},
        task_id="old-task",
        invocation_id="inv-old",
        spawn_id=None,
    )
    table.add_event(
        "sess-mix",
        "User",
        "query",
        {"content": "new raw question"},
        task_id="new-task",
        invocation_id="inv-new",
        spawn_id=None,
    )
    table.add_event(
        "sess-mix",
        "MatMaster",
        "user_turn_context",
        {
            "schema_version": "user_turn_context.v1",
            "kind": "anchor",
            "message": UserMessage(
                content="new rendered question with instructions"
            ).model_dump(mode="json"),
            "user_instructions_hash": "sha256:new",
            "transform": "raw",
            "render_version": "user_context_render.v1",
        },
        task_id="new-task",
        invocation_id="inv-new",
        spawn_id=None,
    )
    table.add_event(
        "sess-mix",
        "MatMaster",
        "response",
        {"content": "new answer"},
        task_id="new-task",
        invocation_id="inv-new",
        spawn_id=None,
    )

    history = ModelHistoryRestoreService(table).restore_history(
        session_id="sess-mix",
        spawn_id=None,
        task_id=None,
    )

    user_messages = [m for m in history if isinstance(m, UserMessage)]
    assert len(user_messages) == 2
    assert user_messages[0].content == "old raw question"
    assert user_messages[1].content == "new rendered question with instructions"


@pytest.mark.asyncio
async def test_restore_with_checkpoint_plus_incremental_events() -> None:
    session_id = "sess-recovery"
    events_table = InMemoryEventsTable()
    fanout = Mock()
    fanout.flush_persistence_barrier = AsyncMock()

    _seed_scope_events(
        events_table,
        session_id=session_id,
        user_content="old question before compaction",
        response_content="old answer before compaction",
    )

    checkpoint_sink = HistoryCheckpointService(events_table).build_checkpoint_sink(
        fanout=fanout,
        session_id=session_id,
        task_id="task-compaction",
        invocation_id="inv-compaction",
        spawn_id=None,
    )
    checkpoint_base_messages = serialize_base_messages(
        [
            _compact_user_message("Recovered summary"),
        ]
    )

    await checkpoint_sink(
        payload={
            "durability": "durable",
            "strategy": "summary",
            "schema_version": "history_checkpoint.v1",
            "covered_until_event_id": 2,
        },
        base_messages=checkpoint_base_messages,
    )

    _seed_scope_events(
        events_table,
        session_id=session_id,
        task_id="task-follow-up",
        user_content="incremental follow-up question",
        response_content="incremental follow-up answer",
        write_user_turn_context=True,
    )

    history = ModelHistoryRestoreService(events_table).restore_history(
        session_id=session_id,
        spawn_id=None,
        task_id=None,
    )

    assert [type(message) for message in history] == [
        UserMessage,
        UserMessage,
        AssistantMessage,
    ]
    assert "Recovered summary" in (history[0].content or "")
    assert [message.content for message in history[1:]] == [
        "incremental follow-up question",
        "incremental follow-up answer",
    ]
    fanout.flush_persistence_barrier.assert_awaited_once()
    assert (
        "get_scope_events_after_id",
        session_id,
        None,
        2,
        None,
    ) in events_table.calls


@pytest.mark.asyncio
async def test_compaction_checkpoint_assembles_v1_session_attachments(
    tmp_path,
) -> None:
    session_id = "sess-attachment-delta"
    events_table = InMemoryEventsTable()
    fanout = Mock()
    fanout.flush_persistence_barrier = AsyncMock()

    events_table.add_event(
        session_id,
        "User",
        "query",
        {
            "content": "old upload before previous checkpoint",
            "files": ["https://oss.example.com/chat/file_1.cif"],
        },
    )
    events_table.add_event(
        session_id,
        "User",
        "query",
        {
            "content": "new upload after previous checkpoint",
            "files": ["https://oss.example.com/chat/file_2.cif"],
        },
        task_id="task-after-checkpoint",
    )
    events_table.add_event(
        session_id,
        "MatMaster",
        "response",
        "new upload acknowledged",
        task_id="task-after-checkpoint",
    )

    compactor = _make_compactor_for_table(
        events_table=events_table,
        session_id=session_id,
        tmp_path=tmp_path,
        provider=_SummaryProvider(
            "Recovered summary with fresh attachment",
        ),
    )

    messages = [
        SystemMessage(content="system prompt"),
        UserMessage(content="continue from current session"),
        AssistantMessage(content="working"),
    ]
    result = await compactor.apply_summary(
        CompactionPlan(
            compaction_id="task-1:root:1",
            compaction_count=1,
            phase="runtime",
            trigger_tokens=1000,
            turn=2,
        ),
        messages,
        "Recovered summary with fresh attachment",
    )

    assert result.base_snapshot is not None
    assert result.checkpoint_covered_until_event_id is not None
    checkpoint_sink = HistoryCheckpointService(events_table).build_checkpoint_sink(
        fanout=fanout,
        session_id=session_id,
        task_id="task-1",
        invocation_id="inv-1",
        spawn_id=None,
    )
    await checkpoint_sink(
        payload={
            "durability": "durable",
            "strategy": "summary",
            "schema_version": "history_checkpoint.v1",
            "covered_until_event_id": result.checkpoint_covered_until_event_id,
            "render_version": "user_context_render.v1",
            "user_instructions_text": result.user_instructions_text,
            "user_instructions_hash": result.user_instructions_hash,
        },
        base_messages=result.base_snapshot,
    )

    checkpoint = events_table.history_checkpoints(spawn_id=None)[0]
    checkpoint_content = checkpoint["content"]
    assert checkpoint_content["schema_version"] == "history_checkpoint.v1"
    assert checkpoint_content["render_version"] == "user_context_render.v1"
    assert checkpoint_content["user_instructions_text"] == "Use SI units."
    assert checkpoint_content["user_instructions_hash"] == "sha256:abc"
    assert checkpoint_content[
        "covered_until_event_id"
    ] == events_table.get_latest_scope_event_id(session_id, None)
    base_messages = checkpoint["content"]["base_messages"]
    assert [message["role"] for message in base_messages] == ["user"]
    content = base_messages[0]["content"]
    assert "<compacted_history>" in content
    assert "<previous_session_summary>" not in content
    assert "[Compacted Context]" not in content
    assert "<attachments>" in content
    assert "file_1" in content
    assert "file_2" in content


@pytest.mark.asyncio
async def test_two_v1_compactions_chain_and_restore_from_latest(
    tmp_path,
) -> None:
    session_id = "sess-two-compactions"
    events_table = InMemoryEventsTable()
    fanout = Mock()
    fanout.flush_persistence_barrier = AsyncMock()

    _seed_scope_events(
        events_table,
        session_id=session_id,
        user_content="first old question",
        response_content="first old answer",
    )

    first_provider = _SummaryProvider("first summary")
    first_compactor = _make_compactor_for_table(
        events_table=events_table,
        session_id=session_id,
        tmp_path=tmp_path,
        provider=first_provider,
    )
    first_messages = [
        SystemMessage(content="system prompt"),
        UserMessage(content="first old question"),
        AssistantMessage(content="first old answer"),
    ]
    first_result = await first_compactor.apply_summary(
        CompactionPlan(
            compaction_id="task-1:root:1",
            compaction_count=1,
            phase="runtime",
            trigger_tokens=1000,
            turn=2,
        ),
        first_messages,
        first_provider.summary,
    )
    assert first_result.checkpoint_covered_until_event_id is not None
    first_sink = HistoryCheckpointService(events_table).build_checkpoint_sink(
        fanout=fanout,
        session_id=session_id,
        task_id="task-1",
        invocation_id="inv-1",
        spawn_id=None,
    )
    await first_sink(
        payload={
            "durability": "durable",
            "strategy": "summary",
            "schema_version": "history_checkpoint.v1",
            "covered_until_event_id": first_result.checkpoint_covered_until_event_id,
            "render_version": "user_context_render.v1",
            "user_instructions_text": first_result.user_instructions_text,
            "user_instructions_hash": first_result.user_instructions_hash,
        },
        base_messages=first_result.base_snapshot,
    )

    _seed_scope_events(
        events_table,
        session_id=session_id,
        task_id="task-between",
        user_content="question between compactions",
        response_content="answer between compactions",
    )

    second_provider = _SummaryProvider("second summary")
    second_compactor = _make_compactor_for_table(
        events_table=events_table,
        session_id=session_id,
        tmp_path=tmp_path,
        provider=second_provider,
    )
    second_messages = [
        SystemMessage(content="system prompt"),
        UserMessage(content=first_result.base_snapshot[0]["content"]),
        AssistantMessage(content="answer between compactions"),
        UserMessage(content="question between compactions"),
    ]
    second_result = await second_compactor.apply_summary(
        CompactionPlan(
            compaction_id="task-2:root:1",
            compaction_count=1,
            phase="runtime",
            trigger_tokens=1000,
            turn=3,
        ),
        second_messages,
        second_provider.summary,
    )
    assert second_result.checkpoint_covered_until_event_id is not None
    second_sink = HistoryCheckpointService(events_table).build_checkpoint_sink(
        fanout=fanout,
        session_id=session_id,
        task_id="task-2",
        invocation_id="inv-2",
        spawn_id=None,
    )
    await second_sink(
        payload={
            "durability": "durable",
            "strategy": "summary",
            "schema_version": "history_checkpoint.v1",
            "covered_until_event_id": second_result.checkpoint_covered_until_event_id,
            "render_version": "user_context_render.v1",
            "user_instructions_text": second_result.user_instructions_text,
            "user_instructions_hash": second_result.user_instructions_hash,
        },
        base_messages=second_result.base_snapshot,
    )

    _seed_scope_events(
        events_table,
        session_id=session_id,
        task_id="task-after-second",
        user_content="question after second checkpoint",
        response_content="answer after second checkpoint",
        write_user_turn_context=True,
    )

    checkpoints = events_table.history_checkpoints(spawn_id=None)
    assert len(checkpoints) == 2
    latest = checkpoints[-1]["content"]
    assert "second summary" in latest["base_messages"][0]["content"]

    restored = ModelHistoryRestoreService(events_table).restore_history(
        session_id=session_id,
        spawn_id=None,
        task_id=None,
    )
    user_messages = [
        message for message in restored if isinstance(message, UserMessage)
    ]
    assert "second summary" in (user_messages[0].content or "")
    assert "first summary" not in (user_messages[0].content or "")
    assert any(
        message.content == "question after second checkpoint"
        for message in user_messages[1:]
    )


@pytest.mark.asyncio
async def test_ephemeral_compaction_does_not_trigger_checkpoint_sink() -> None:
    session_id = "sess-ephemeral"
    events_table = InMemoryEventsTable()
    fanout = Mock()
    fanout.flush_persistence_barrier = AsyncMock()

    _seed_scope_events(
        events_table,
        session_id=session_id,
        user_content="question before fallback",
        response_content="answer before fallback",
    )

    checkpoint_sink = HistoryCheckpointService(events_table).build_checkpoint_sink(
        fanout=fanout,
        session_id=session_id,
        task_id="task-runtime",
        invocation_id="inv-runtime",
        spawn_id=None,
    )
    base_messages = serialize_base_messages(
        [SystemMessage(content="[Compacted Context]\nShould never persist")]
    )

    await checkpoint_sink(
        payload={"durability": "ephemeral", "strategy": "sliding_window"},
        base_messages=base_messages,
    )
    await checkpoint_sink(
        payload={"durability": "ephemeral", "strategy": "tool_truncation"},
        base_messages=base_messages,
    )

    fanout.flush_persistence_barrier.assert_not_awaited()
    assert events_table.history_checkpoints(spawn_id=None) == []
    assert events_table.compact_boundaries(spawn_id=None) == []
    assert not any(
        call[0] == "get_latest_scope_event_id" for call in events_table.calls
    )
    assert not any(call[0] == "add_checkpoint_pair" for call in events_table.calls)


@pytest.mark.asyncio
async def test_spawn_id_checkpoint_does_not_affect_parent_restore() -> None:
    session_id = "sess-spawn-scope"
    child_spawn_id = "child-1"
    events_table = InMemoryEventsTable()
    fanout = Mock()
    fanout.flush_persistence_barrier = AsyncMock()

    _seed_scope_events(
        events_table,
        session_id=session_id,
        user_content="parent raw question",
        response_content="parent raw answer",
    )
    _seed_scope_events(
        events_table,
        session_id=session_id,
        spawn_id=child_spawn_id,
        user_content="child question before checkpoint",
        response_content="child answer before checkpoint",
    )

    child_sink = HistoryCheckpointService(events_table).build_checkpoint_sink(
        fanout=fanout,
        session_id=session_id,
        task_id="task-child",
        invocation_id="inv-child",
        spawn_id=child_spawn_id,
    )
    child_base_messages = serialize_base_messages(
        [
            _compact_user_message("child summary"),
        ]
    )
    child_covered_until = events_table.get_latest_scope_event_id(
        session_id,
        child_spawn_id,
    )

    await child_sink(
        payload={
            "durability": "durable",
            "strategy": "summary",
            "schema_version": "history_checkpoint.v1",
            "covered_until_event_id": child_covered_until,
        },
        base_messages=child_base_messages,
    )

    _seed_scope_events(
        events_table,
        session_id=session_id,
        spawn_id=child_spawn_id,
        task_id="task-child-follow-up",
        user_content="child incremental question",
        response_content="child incremental answer",
        write_user_turn_context=True,
    )

    restore_service = ModelHistoryRestoreService(events_table)
    parent_history = restore_service.restore_history(
        session_id=session_id,
        spawn_id=None,
        task_id=None,
    )
    child_history = restore_service.restore_history(
        session_id=session_id,
        spawn_id=child_spawn_id,
        task_id=None,
    )

    assert [type(message) for message in parent_history] == [
        UserMessage,
        AssistantMessage,
    ]
    assert [message.content for message in parent_history] == [
        "parent raw question",
        "parent raw answer",
    ]
    assert all("child" not in str(message.content) for message in parent_history)
    assert [type(message) for message in child_history] == [
        UserMessage,
        UserMessage,
        AssistantMessage,
    ]
    assert "child summary" in (child_history[0].content or "")
    assert [message.content for message in child_history[1:]] == [
        "child incremental question",
        "child incremental answer",
    ]
