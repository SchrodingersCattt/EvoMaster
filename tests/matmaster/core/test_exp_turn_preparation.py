from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from matmaster.config.exp import ExpConfig
from matmaster.context.ports import SessionEvent, UserInstructions, hash_user_instructions
from matmaster.context.sources.turn_input import TurnInput
from matmaster.core.exp import Exp
from matmaster.core.playground import ExecutionEnvironment
from matmaster.core.run_context import AgentRunContext, AgentRunRequest
from matmaster.types.events import RunResultEvent
from matmaster.types.messages import LLMResponse, StreamChunk
from matmaster.types.runtime_ports import AgentRunPorts, PlaygroundCompactionPort


class _Provider:
    stream_timeout = 10.0
    max_retries = 1
    retry_delay = 0.0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None):
        return LLMResponse(content="mock", finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="ok")
        yield StreamChunk(finish_reason="stop")


class _History:
    def __init__(self, events: tuple[SessionEvent, ...] = ()) -> None:
        self.events = events
        self.queries = []

    async def load_events(self, query):
        self.queries.append(query)
        if query.event_types == ("skill_hit",):
            return tuple(
                event for event in self.events if event.event_type == "skill_hit"
            )
        return tuple(
            event
            for event in self.events
            if event.event_type in {"user_turn_context", "history_checkpoint"}
        )

    def query_events(self):
        return []

    def all_events(self):
        return []

    def latest_checkpoint_covered_until_event_id(self):
        return None

    def latest_scope_event_id(self):
        return None


def _ctx(
    tmp_path: Path,
    *,
    turn_input: TurnInput | None,
    user_instructions: UserInstructions | None = None,
    history: _History | None = None,
    writer: Any | None = None,
) -> AgentRunContext:
    return AgentRunContext(
        environment=ExecutionEnvironment(
            workdir=tmp_path,
            execution_workdir=str(tmp_path),
            session_type="local",
            cache_area=tmp_path / "cache",
        ),
        request=AgentRunRequest(
            invocation_id="inv-1",
            llm_provider=_Provider(),
            turn_input=turn_input,
            user_instructions=user_instructions,
            ports=AgentRunPorts(
                compaction=PlaygroundCompactionPort(history=history),
                user_turn_context_writer=writer,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_root_run_missing_turn_input_fails_before_runtime_build(tmp_path: Path):
    exp = Exp(ExpConfig(name="test"))

    with pytest.raises(RuntimeError, match="turn_input is required"):
        async for _event in exp.run_stream(_ctx(tmp_path, turn_input=None)):
            pass


@pytest.mark.asyncio
async def test_root_run_renders_and_writes_user_turn_context(tmp_path: Path):
    calls = []

    async def writer(request):
        calls.append(request)

    instructions = UserInstructions(
        text="Use SI units.",
        hash=hash_user_instructions("Use SI units."),
        truncated=False,
    )
    ctx = _ctx(
        tmp_path,
        turn_input=TurnInput.from_values(user_text="hello"),
        user_instructions=instructions,
        history=_History(),
        writer=writer,
    )

    events = [event async for event in Exp(ExpConfig(name="test")).run_stream(ctx)]

    assert any(isinstance(event, RunResultEvent) for event in events)
    assert len(calls) == 1
    assert calls[0].kind == "anchor"
    assert calls[0].invocation_id == "inv-1"
    assert calls[0].user_instructions_hash == instructions.hash
    assert "Use SI units." in calls[0].message.content
    assert "hello" in calls[0].message.content


@pytest.mark.asyncio
async def test_writer_failure_propagates(tmp_path: Path):
    async def writer(_request):
        raise RuntimeError("write failed")

    ctx = _ctx(
        tmp_path,
        turn_input=TurnInput.from_values(user_text="hello"),
        history=_History(),
        writer=writer,
    )

    with pytest.raises(RuntimeError, match="write failed"):
        async for _event in Exp(ExpConfig(name="test")).run_stream(ctx):
            pass


@pytest.mark.asyncio
async def test_spawn_run_does_not_write_user_turn_context(tmp_path: Path):
    calls = []

    async def writer(request):
        calls.append(request)

    ctx = _ctx(
        tmp_path,
        turn_input=TurnInput.from_values(user_text="root"),
        history=_History(),
        writer=writer,
    )

    events = [
        event
        async for event in Exp(ExpConfig(name="test")).run_stream(
            ctx,
            "child task",
            spawn_id="child-1",
        )
    ]

    assert any(isinstance(event, RunResultEvent) for event in events)
    assert calls == []


@pytest.mark.asyncio
async def test_root_run_falls_back_when_history_and_instructions_are_missing(
    tmp_path: Path,
):
    calls = []

    async def writer(request):
        calls.append(request)

    ctx = _ctx(
        tmp_path,
        turn_input=TurnInput.from_values(user_text="hello"),
        user_instructions=None,
        history=None,
        writer=writer,
    )

    events = [event async for event in Exp(ExpConfig(name="test")).run_stream(ctx)]

    assert any(isinstance(event, RunResultEvent) for event in events)
    assert len(calls) == 1
    assert calls[0].user_instructions_hash == hash_user_instructions("")
