"""Tests for AgentKernel checkpoint-aware compaction and Exp scope resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from matmaster.context.sources.turn_input import TurnInput
from matmaster.types.messages import (
    AssistantMessage,
    LLMResponse,
    StreamChunk,
    UserMessage,
)
from matmaster.types.run_metadata import RunIdentity, RunMetadata
from matmaster.types.runtime_ports import KernelRuntimePorts

from .agent_kernel_test_helpers import make_kernel_runtime, make_kernel_turn

# ── Providers ────────────────────────────────────────────────


class ContentOnlyProvider:
    """Provider that only streams content, no reasoning."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None, *, tool_choice=None):
        return LLMResponse(content="not used", finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="hello ")
        yield StreamChunk(content="world")
        yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 5})


# ── Compactor test doubles ───────────────────────────────────


class FailingSummaryProvider(ContentOnlyProvider):
    async def chat(self, messages, tools=None, *, tool_choice=None):
        raise RuntimeError("summary down")


def _v1_compacted_user_message(summary: str = "summary") -> UserMessage:
    return UserMessage(content=f"<compacted_history>\n{summary}\n</compacted_history>")


class _DurablePreflightCompactor:
    """Compactor test double that emits one durable preflight event."""

    def __init__(self) -> None:
        self.preflight_calls = 0
        self.runtime_calls = 0
        self.message_counts: list[int] = []

    def update_message_count(self, count: int) -> None:
        self.message_counts.append(count)

    def plan_preflight_compaction(self, messages: list[Any]):
        from matmaster.context.compaction import CompactionPlan

        self.preflight_calls += 1
        return CompactionPlan(
            compaction_id="task-1:root:1",
            compaction_count=1,
            phase="preflight",
            trigger_tokens=1234,
            turn=0,
        )

    async def apply_summary(
        self,
        plan,
        messages: list[Any],
        summary: str,
        *,
        turn_input=None,
    ):
        from matmaster.context.compaction import CompactionResult

        compact_message = _v1_compacted_user_message()
        base_messages = [
            compact_message.model_dump(mode="json"),
        ]
        messages[:] = [
            messages[0],
            compact_message,
        ]
        return CompactionResult(
            compaction_id=plan.compaction_id,
            compaction_count=plan.compaction_count,
            phase=plan.phase,
            strategy="summary",
            durability="durable",
            trigger_tokens=plan.trigger_tokens,
            retained_turns=1,
            failure_reason=None,
            base_messages=base_messages,
            checkpoint_covered_until_event_id=41,
            user_instructions_text="Use SI units.",
            user_instructions_hash="sha256:abc",
        )

    async def plan_runtime_compaction(
        self, messages: list[Any], turn_usage: dict[str, int], *, turn: int
    ):
        self.runtime_calls += 1
        return None


class _LifecycleCompactor:
    """Compactor test double for running -> complete lifecycle orchestration."""

    def __init__(self, summary_text: str) -> None:
        self._summary_text = summary_text
        self.message_counts: list[int] = []
        self.plan_calls = 0
        self.apply_calls = 0

    def update_message_count(self, count: int) -> None:
        self.message_counts.append(count)

    async def plan_runtime_compaction(
        self,
        messages: list[Any],
        turn_usage: dict[str, int],
        *,
        turn: int,
    ):
        from matmaster.context.compaction import CompactionPlan

        self.plan_calls += 1
        return CompactionPlan(
            compaction_id="task-1:root:1",
            compaction_count=1,
            phase="runtime",
            trigger_tokens=950,
            turn=turn,
        )

    async def apply_summary(
        self,
        plan,
        messages: list[Any],
        summary: str,
        *,
        turn_input=None,
    ):
        from matmaster.context.compaction import CompactionResult

        self.apply_calls += 1
        compact_message = _v1_compacted_user_message(self._summary_text)
        messages[:] = [
            messages[0],
            compact_message,
        ]
        return CompactionResult(
            compaction_id=plan.compaction_id,
            compaction_count=plan.compaction_count,
            phase=plan.phase,
            strategy="summary",
            durability="durable",
            trigger_tokens=plan.trigger_tokens,
            retained_turns=1,
            failure_reason=None,
            base_messages=[
                compact_message.model_dump(mode="json"),
            ],
            checkpoint_covered_until_event_id=41,
            user_instructions_text="Use SI units.",
            user_instructions_hash="sha256:abc",
        )


class _EphemeralFallbackCompactor(_LifecycleCompactor):
    async def apply_fallback(
        self,
        plan,
        messages: list[Any],
        *,
        failure_reason: str,
    ):
        from matmaster.context.compaction import CompactionResult

        self.apply_calls += 1
        return CompactionResult(
            compaction_id=plan.compaction_id,
            compaction_count=plan.compaction_count,
            phase=plan.phase,
            strategy="sliding_window",
            durability="ephemeral",
            trigger_tokens=plan.trigger_tokens,
            retained_turns=0,
            failure_reason=failure_reason,
            base_messages=None,
        )


def _build_long_history() -> list[Any]:
    return [
        UserMessage(content="old question 1"),
        AssistantMessage(content="old answer 1"),
        UserMessage(content="old question 2"),
        AssistantMessage(content="old answer 2"),
    ]


def build_runtime_spec_with_compaction(*, checkpoint_sink: Any, summary_text: str):
    from matmaster.types.runtime_ports import KernelRuntimePorts

    return make_kernel_runtime(
        provider=ContentOnlyProvider(),
        compactor=_LifecycleCompactor(summary_text),
        run_identity=RunIdentity(task_id="task-1"),
        runtime_ports=KernelRuntimePorts(checkpoint_sink=checkpoint_sink),
    )


# ── TestCheckpointAwareCompaction ────────────────────────────


class TestCheckpointAwareCompaction:
    @pytest.mark.asyncio
    async def test_durable_compaction_emits_running_then_complete_after_checkpoint(
        self,
    ) -> None:
        from matmaster.core.agent import AgentKernel

        events: list[object] = []
        checkpoint_calls: list[tuple[dict, list[dict]]] = []

        async def checkpoint_sink(*, payload: dict, base_messages: list[dict]) -> None:
            checkpoint_calls.append((payload, base_messages))

        runtime = build_runtime_spec_with_compaction(
            checkpoint_sink=checkpoint_sink,
            summary_text="compacted summary",
        )

        async for event in AgentKernel().run_stream(
            runtime, make_kernel_turn("task"), history=_build_long_history()
        ):
            events.append(event)

        compaction_events = [
            e for e in events if getattr(e, "type", None) == "compaction"
        ]
        assert [e.status for e in compaction_events] == ["running", "complete"]
        assert (
            checkpoint_calls
        ), "checkpoint sink should be called before complete event"
        assert compaction_events[1].checkpoint_written is True

    @pytest.mark.asyncio
    async def test_checkpoint_failure_keeps_complete_event_but_marks_failure(
        self,
    ) -> None:
        from matmaster.core.agent import AgentKernel

        events: list[object] = []

        async def checkpoint_sink(*, payload: dict, base_messages: list[dict]) -> None:
            raise RuntimeError("checkpoint store down")

        runtime = build_runtime_spec_with_compaction(
            checkpoint_sink=checkpoint_sink,
            summary_text="compacted summary",
        )

        async for event in AgentKernel().run_stream(
            runtime, make_kernel_turn("task"), history=_build_long_history()
        ):
            events.append(event)

        compaction_events = [
            e for e in events if getattr(e, "type", None) == "compaction"
        ]
        assert [e.status for e in compaction_events] == ["running", "complete"]
        assert compaction_events[-1].checkpoint_written is False
        assert compaction_events[-1].failure_reason == "checkpoint store down"

    @pytest.mark.asyncio
    async def test_fallback_compaction_event_carries_strategy_and_durability(
        self,
    ) -> None:
        from matmaster.core.agent import AgentKernel

        checkpoint_sink = pytest.fail
        kernel_runtime = make_kernel_runtime(
            provider=FailingSummaryProvider(),
            compactor=_EphemeralFallbackCompactor("ignored"),
            runtime_ports=KernelRuntimePorts(checkpoint_sink=checkpoint_sink),
            run_identity=RunIdentity(task_id="task-1"),
        )

        events = [
            event
            async for event in AgentKernel().run_stream(
                kernel_runtime,
                make_kernel_turn("task"),
                history=_build_long_history(),
            )
        ]

        complete = [
            event
            for event in events
            if getattr(event, "type", None) == "compaction"
            and getattr(event, "status", None) == "complete"
        ][0]
        assert complete.strategy == "sliding_window"
        assert complete.durability == "ephemeral"
        assert complete.failure_reason == "summary down"
        assert complete.checkpoint_written is False

    @pytest.mark.asyncio
    async def test_kernel_preflight_calls_checkpoint_sink_for_durable_compaction(
        self,
    ) -> None:
        from matmaster.core.agent import AgentKernel

        provider = ContentOnlyProvider()
        compactor = _DurablePreflightCompactor()
        checkpoint_calls: list[dict[str, Any]] = []

        async def checkpoint_sink(
            *, payload: dict[str, Any], base_messages: list[dict[str, Any]]
        ) -> None:
            checkpoint_calls.append(
                {
                    "payload": payload,
                    "base_messages": base_messages,
                }
            )

        kernel_runtime = make_kernel_runtime(
            provider=provider,
            compactor=compactor,
            runtime_ports=KernelRuntimePorts(checkpoint_sink=checkpoint_sink),
        )

        kernel = AgentKernel()
        events: list[Any] = []
        async for event in kernel.run_stream(
            kernel_runtime,
            make_kernel_turn("test task"),
            history=[
                UserMessage(content="old question"),
                AssistantMessage(content="old answer"),
            ],
        ):
            events.append(event)

        assert compactor.preflight_calls == 1
        assert checkpoint_calls == [
            {
                "payload": {
                    "durability": "durable",
                    "strategy": "summary",
                    "schema_version": "history_checkpoint.v1",
                    "render_version": "user_context_render.v1",
                    "user_instructions_text": "Use SI units.",
                    "user_instructions_hash": "sha256:abc",
                    "covered_until_event_id": 41,
                },
                "base_messages": [
                    _v1_compacted_user_message().model_dump(mode="json"),
                ],
            }
        ]
        assert any(getattr(event, "type", None) == "compaction" for event in events)

    @pytest.mark.asyncio
    async def test_kernel_yields_compaction_event_before_checkpoint_sink(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = ContentOnlyProvider()
        compactor = _DurablePreflightCompactor()
        sequence: list[str] = []

        async def checkpoint_sink(
            *, payload: dict[str, Any], base_messages: list[dict[str, Any]]
        ) -> None:
            sequence.append("sink")

        kernel_runtime = make_kernel_runtime(
            provider=provider,
            compactor=compactor,
            runtime_ports=KernelRuntimePorts(checkpoint_sink=checkpoint_sink),
        )

        kernel = AgentKernel()
        async for event in kernel.run_stream(
            kernel_runtime,
            make_kernel_turn("test task"),
            history=[
                UserMessage(content="old question"),
                AssistantMessage(content="old answer"),
            ],
        ):
            if getattr(event, "type", None) == "compaction":
                sequence.append("event")

        assert sequence == ["event", "sink", "event"]

    @pytest.mark.asyncio
    async def test_kernel_updates_compaction_payload_when_checkpoint_sink_fails(
        self,
    ) -> None:
        from matmaster.core.agent import AgentKernel

        provider = ContentOnlyProvider()
        compactor = _DurablePreflightCompactor()
        sequence: list[str] = []
        compaction_event = None

        async def checkpoint_sink(
            *, payload: dict[str, Any], base_messages: list[dict[str, Any]]
        ) -> None:
            sequence.append("sink")
            raise RuntimeError("checkpoint unavailable")

        kernel_runtime = make_kernel_runtime(
            provider=provider,
            compactor=compactor,
            runtime_ports=KernelRuntimePorts(checkpoint_sink=checkpoint_sink),
        )

        kernel = AgentKernel()
        async for event in kernel.run_stream(
            kernel_runtime,
            make_kernel_turn("test task"),
            history=[
                UserMessage(content="old question"),
                AssistantMessage(content="old answer"),
            ],
        ):
            if getattr(event, "type", None) == "compaction":
                sequence.append("event")
                compaction_event = event

        assert sequence == ["event", "sink", "event"]
        assert compaction_event is not None
        assert compaction_event.status == "complete"
        assert compaction_event.checkpoint_written is False
        assert compaction_event.failure_reason == "checkpoint unavailable"


@pytest.mark.asyncio
async def test_kernel_reads_checkpoint_sink_from_runtime_ports() -> None:
    from matmaster.core.agent import AgentKernel
    from matmaster.types.runtime_ports import KernelRuntimePorts

    provider = ContentOnlyProvider()
    compactor = _DurablePreflightCompactor()
    checkpoint_calls: list[dict[str, Any]] = []

    async def checkpoint_sink(
        *, payload: dict[str, Any], base_messages: list[dict[str, Any]]
    ) -> int | None:
        checkpoint_calls.append(
            {
                "payload": payload,
                "base_messages": base_messages,
            }
        )
        return 99

    kernel_runtime = make_kernel_runtime(
        provider=provider,
        compactor=compactor,
        runtime_ports=KernelRuntimePorts(checkpoint_sink=checkpoint_sink),
    )

    events = [
        event
        async for event in AgentKernel().run_stream(
            kernel_runtime,
            make_kernel_turn("test task"),
            history=[
                UserMessage(content="old question"),
                AssistantMessage(content="old answer"),
            ],
        )
    ]

    assert checkpoint_calls
    assert any(getattr(event, "covered_until_event_id", None) == 99 for event in events)


class _BarrierFailureCompactor(_DurablePreflightCompactor):
    def __init__(self) -> None:
        super().__init__()
        self.apply_calls = 0

    async def apply_summary(
        self,
        plan,
        messages,
        summary: str,
        *,
        turn_input=None,
    ):
        self.apply_calls += 1
        return await super().apply_summary(
            plan,
            messages,
            summary,
            turn_input=turn_input,
        )


class _BoundaryOverrideCompactor(_DurablePreflightCompactor):
    async def apply_summary(
        self,
        plan,
        messages: list[Any],
        summary: str,
        *,
        turn_input=None,
    ):
        from matmaster.context.compaction import CompactionResult

        compact_message = _v1_compacted_user_message()
        base_messages = [
            compact_message.model_dump(mode="json"),
        ]
        messages[:] = [
            messages[0],
            compact_message,
        ]
        return CompactionResult(
            compaction_id=plan.compaction_id,
            compaction_count=plan.compaction_count,
            phase=plan.phase,
            strategy="summary",
            durability="durable",
            trigger_tokens=plan.trigger_tokens,
            retained_turns=1,
            failure_reason=None,
            base_messages=base_messages,
            checkpoint_covered_until_event_id=41,
            user_instructions_text="Use SI units.",
            user_instructions_hash="sha256:abc",
        )


class _RecordingTurnInputCompactor(_DurablePreflightCompactor):
    def __init__(self) -> None:
        super().__init__()
        self.seen_turn_input: Any = None
        self.apply_calls = 0

    async def apply_summary(
        self,
        plan,
        messages: list[Any],
        summary: str,
        *,
        turn_input=None,
    ):
        self.apply_calls += 1
        self.seen_turn_input = turn_input
        return await super().apply_summary(
            plan,
            messages,
            summary,
            turn_input=turn_input,
        )


@pytest.mark.asyncio
async def test_kernel_passes_raw_turn_input_to_preflight_compactor():
    from matmaster.core.agent import AgentKernel

    compactor = _RecordingTurnInputCompactor()

    async def checkpoint_sink(**kwargs):
        return 42

    turn_input = TurnInput.from_values(
        user_text="original before rewrite",
        files=["https://oss.example.com/chat/current.cif"],
        pre_turn_history_event_id=42,
    )
    kernel_runtime = make_kernel_runtime(
        provider=ContentOnlyProvider(),
        compactor=compactor,
        runtime_ports=KernelRuntimePorts(checkpoint_sink=checkpoint_sink),
    )

    [
        event
        async for event in AgentKernel().run_stream(
            kernel_runtime,
            make_kernel_turn("effective task text", turn_input=turn_input),
            history=[
                UserMessage(content="old question"),
                AssistantMessage(content="old answer"),
            ],
        )
    ]

    assert compactor.seen_turn_input.user_text == "original before rewrite"
    assert compactor.seen_turn_input.files == (
        "https://oss.example.com/chat/current.cif",
    )
    assert compactor.seen_turn_input.pre_turn_history_event_id == 42


@pytest.mark.asyncio
async def test_direct_kernel_keeps_missing_turn_input_explicit_for_preflight_history():
    from matmaster.core.agent import AgentKernel

    compactor = _RecordingTurnInputCompactor()

    async def checkpoint_sink(**kwargs):
        return 42

    kernel_runtime = make_kernel_runtime(
        provider=ContentOnlyProvider(),
        compactor=compactor,
        runtime_ports=KernelRuntimePorts(checkpoint_sink=checkpoint_sink),
    )

    [
        event
        async for event in AgentKernel().run_stream(
            kernel_runtime,
            make_kernel_turn("direct current task"),
            history=[
                UserMessage(content="old question"),
                AssistantMessage(content="old answer"),
            ],
        )
    ]

    assert compactor.seen_turn_input is None


@pytest.mark.asyncio
async def test_kernel_skips_preflight_current_split_when_history_is_empty() -> None:
    from matmaster.core.agent import AgentKernel

    compactor = _RecordingTurnInputCompactor()

    async def checkpoint_sink(**kwargs):
        return 42

    turn_input = TurnInput.from_values(
        user_text="current task",
        files=["https://oss.example.com/chat/current.cif"],
        pre_turn_history_event_id=42,
    )
    kernel_runtime = make_kernel_runtime(
        provider=ContentOnlyProvider(),
        compactor=compactor,
        runtime_ports=KernelRuntimePorts(checkpoint_sink=checkpoint_sink),
    )

    [
        event
        async for event in AgentKernel().run_stream(
            kernel_runtime,
            make_kernel_turn("current task", turn_input=turn_input),
            history=None,
        )
    ]

    assert compactor.preflight_calls == 0
    assert compactor.apply_calls == 0


@pytest.mark.asyncio
async def test_kernel_passes_checkpoint_covered_until_override_to_sink() -> None:
    from matmaster.core.agent import AgentKernel

    checkpoint_calls: list[dict[str, Any]] = []

    async def checkpoint_sink(
        *, payload: dict[str, Any], base_messages: list[dict[str, Any]]
    ) -> int | None:
        checkpoint_calls.append(
            {
                "payload": payload,
                "base_messages": base_messages,
            }
        )
        return payload.get("covered_until_event_id")

    kernel_runtime = make_kernel_runtime(
        provider=ContentOnlyProvider(),
        compactor=_BoundaryOverrideCompactor(),
        runtime_ports=KernelRuntimePorts(checkpoint_sink=checkpoint_sink),
    )

    events = [
        event
        async for event in AgentKernel().run_stream(
            kernel_runtime,
            make_kernel_turn("test task"),
            history=[
                UserMessage(content="old question"),
                AssistantMessage(content="old answer"),
            ],
        )
    ]

    assert checkpoint_calls[0]["payload"] == {
        "durability": "durable",
        "strategy": "summary",
        "schema_version": "history_checkpoint.v1",
        "render_version": "user_context_render.v1",
        "user_instructions_text": "Use SI units.",
        "user_instructions_hash": "sha256:abc",
        "covered_until_event_id": 41,
    }
    assert any(getattr(event, "covered_until_event_id", None) == 41 for event in events)


@pytest.mark.asyncio
async def test_kernel_sync_pre_compaction_barrier_error_stops_compaction() -> None:
    from matmaster.core.agent import AgentKernel
    from matmaster.types.runtime_ports import KernelRuntimePorts

    provider = ContentOnlyProvider()
    compactor = _BarrierFailureCompactor()

    def barrier() -> None:
        raise RuntimeError("barrier failed")

    kernel_runtime = make_kernel_runtime(
        provider=provider,
        compactor=compactor,
        runtime_ports=KernelRuntimePorts(pre_compaction_barrier=barrier),
    )

    with pytest.raises(RuntimeError, match="barrier failed"):
        [
            event
            async for event in AgentKernel().run_stream(
                kernel_runtime,
                make_kernel_turn("test task"),
                history=[
                    UserMessage(content="old question"),
                    AssistantMessage(content="old answer"),
                ],
            )
        ]

    assert compactor.preflight_calls == 1
    assert compactor.apply_calls == 0


@pytest.mark.asyncio
async def test_kernel_async_pre_compaction_barrier_error_stops_compaction() -> None:
    from matmaster.core.agent import AgentKernel
    from matmaster.types.runtime_ports import KernelRuntimePorts

    provider = ContentOnlyProvider()
    compactor = _BarrierFailureCompactor()

    async def barrier() -> None:
        raise RuntimeError("async barrier failed")

    kernel_runtime = make_kernel_runtime(
        provider=provider,
        compactor=compactor,
        runtime_ports=KernelRuntimePorts(pre_compaction_barrier=barrier),
    )

    with pytest.raises(RuntimeError, match="async barrier failed"):
        [
            event
            async for event in AgentKernel().run_stream(
                kernel_runtime,
                make_kernel_turn("test task"),
                history=[
                    UserMessage(content="old question"),
                    AssistantMessage(content="old answer"),
                ],
            )
        ]

    assert compactor.preflight_calls == 1
    assert compactor.apply_calls == 0


# ── TestExpCheckpointSinkScopeResolution ─────────────────────


class TestExpCheckpointSinkScopeResolution:
    @pytest.mark.asyncio
    async def test_build_runtime_resolves_checkpoint_sink_by_spawn_scope(
        self,
        tmp_path: Path,
    ) -> None:
        from matmaster.config.exp import ExpConfig
        from matmaster.core.exp import Exp
        from matmaster.core.playground import ExecutionEnvironment
        from matmaster.core.run_context import AgentRunContext, AgentRunRequest
        from matmaster.types.runtime_ports import (
            AgentRunPorts,
            PlaygroundCompactionPort,
        )

        parent_sink = object()
        child_sink = object()
        seen_spawn_ids: list[str | None] = []

        def checkpoint_sink_factory(*, spawn_id: str | None = None):
            seen_spawn_ids.append(spawn_id)
            if spawn_id == "child-1":
                return child_sink
            return parent_sink

        ctx = AgentRunContext(
            environment=ExecutionEnvironment(
                workdir=tmp_path,
                session_type="local",
                cache_area=tmp_path / "cache",
                execution_workdir=str(tmp_path),
                metadata=RunMetadata(),
            ),
            request=AgentRunRequest(
                llm_provider=ContentOnlyProvider(),
                ports=AgentRunPorts(
                    compaction=PlaygroundCompactionPort(
                        checkpoint_sink_factory=checkpoint_sink_factory,
                    )
                ),
            ),
        )
        exp = Exp(ExpConfig(name="test"))

        with patch("matmaster.core.agent.AgentKernel"):
            parent_runtime = await exp.build_runtime(ctx, spawn_id=None)
            child_runtime = await exp.build_runtime(ctx, spawn_id="child-1")

        assert seen_spawn_ids == [None, "child-1"]
        assert (
            parent_runtime.kernel_runtime.resources.runtime_ports.checkpoint_sink
            is parent_sink
        )
        assert (
            child_runtime.kernel_runtime.resources.runtime_ports.checkpoint_sink
            is child_sink
        )


@pytest.mark.asyncio
async def test_kernel_runs_pre_compaction_barrier_before_compactor() -> None:
    from matmaster.core.agent import AgentKernel
    from matmaster.types.runtime_ports import KernelRuntimePorts

    sequence: list[str] = []

    class BarrierCompactor(_DurablePreflightCompactor):
        async def apply_summary(
            self,
            plan,
            messages,
            summary: str,
            *,
            turn_input=None,
        ):
            sequence.append("apply")
            return await super().apply_summary(
                plan,
                messages,
                summary,
                turn_input=turn_input,
            )

    async def barrier() -> None:
        sequence.append("barrier")

    kernel_runtime = make_kernel_runtime(
        provider=ContentOnlyProvider(),
        compactor=BarrierCompactor(),
        runtime_ports=KernelRuntimePorts(
            pre_compaction_barrier=barrier,
            checkpoint_sink=lambda **kwargs: None,
        ),
    )

    async for _event in AgentKernel().run_stream(
        kernel_runtime,
        make_kernel_turn("task"),
        history=[UserMessage(content="old"), AssistantMessage(content="answer")],
    ):
        pass

    assert sequence[:2] == ["barrier", "apply"]
