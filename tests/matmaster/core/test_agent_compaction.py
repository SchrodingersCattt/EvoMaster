from __future__ import annotations

import pytest

from matmaster.context.compaction import CompactionPlan
from matmaster.core.agent_compaction import run_compaction_plan
from matmaster.core.kernel_items import _KernelState
from matmaster.types.events import CompactionEvent
from matmaster.types.messages import SystemMessage, ToolCallData, UserMessage
from matmaster.types.run_metadata import RunIdentity
from matmaster.types.runtime import (
    AgentKernelResources,
    AgentKernelSpec,
    CompactionConfig,
)
from matmaster.types.runtime_ports import KernelRuntimePorts


class Provider:
    def __init__(
        self,
        content: str | Exception = "summary",
        *,
        tool_calls: list[ToolCallData] | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls
        self.calls = []

    async def chat(self, messages, tools=None, *, tool_choice=None):
        self.calls.append((messages, tools, tool_choice))
        if isinstance(self.content, Exception):
            raise self.content
        from matmaster.types.messages import LLMResponse

        return LLMResponse(
            content=self.content,
            finish_reason="stop",
            tool_calls=self.tool_calls,
        )


class Compactor:
    def __init__(self) -> None:
        self.summary_calls = []
        self.fallback_calls = []

    async def apply_summary(self, plan, messages, summary, *, turn_input=None):
        self.summary_calls.append((plan, list(messages), summary, turn_input))
        from matmaster.context.compaction import CompactionResult

        messages[:] = [
            messages[0],
            UserMessage(content="<compacted_history>summary</compacted_history>"),
        ]
        return CompactionResult(
            compaction_id=plan.compaction_id,
            compaction_count=plan.compaction_count,
            phase=plan.phase,
            strategy="summary",
            durability="durable",
            trigger_tokens=plan.trigger_tokens,
            retained_turns=0,
            failure_reason=None,
            base_messages=[messages[1].model_dump(mode="json")],
        )

    async def apply_fallback(self, plan, messages, *, failure_reason):
        self.fallback_calls.append((plan, failure_reason))
        from matmaster.context.compaction import CompactionResult

        return CompactionResult(
            compaction_id=plan.compaction_id,
            compaction_count=plan.compaction_count,
            phase=plan.phase,
            strategy="sliding_window",
            durability="ephemeral",
            trigger_tokens=plan.trigger_tokens,
            retained_turns=len(messages) - 1,
            failure_reason=failure_reason,
            base_messages=None,
        )


def _kernel_spec() -> AgentKernelSpec:
    return AgentKernelSpec(
        system_prompt="sys",
        max_turns=10,
        compaction=CompactionConfig(
            context_limit=20_000, reserved_summary_tokens=1_000
        ),
        run_identity=RunIdentity(),
    )


def _kernel_resources(
    provider: Provider, compactor: Compactor
) -> AgentKernelResources:
    return AgentKernelResources(
        llm_provider=provider,
        runtime_ports=KernelRuntimePorts(),
        tool_runner=None,
        tool_catalog=None,
        runtime_topology=None,
        compactor=compactor,
    )


def _plan(phase: str) -> CompactionPlan:
    return CompactionPlan(
        compaction_id="root:1",
        compaction_count=1,
        phase=phase,
        trigger_tokens=123,
        turn=2,
    )


@pytest.mark.asyncio
async def test_compaction_plan_runner_summary_success_calls_apply_summary() -> None:
    provider = Provider("summary text")
    compactor = Compactor()
    state = _KernelState(
        messages=[SystemMessage(content="sys"), UserMessage(content="old")]
    )
    tools = [{"type": "function", "function": {"name": "tool"}}]

    events = [
        item.event
        async for item in run_compaction_plan(
            kernel_spec=_kernel_spec(),
            kernel_resources=_kernel_resources(provider, compactor),
            state=state,
            plan=_plan("runtime"),
            checkpoint_sink=None,
            tool_definitions=tools,
        )
    ]

    assert [event.status for event in events if isinstance(event, CompactionEvent)] == [
        "running",
        "complete",
    ]
    assert provider.calls[0][1] is tools
    assert provider.calls[0][2] == "none"
    assert compactor.summary_calls[0][2] == "summary text"
    assert compactor.fallback_calls == []


@pytest.mark.asyncio
async def test_compaction_plan_runner_passes_configured_summary_safety_margin(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_call_summary_llm(**kwargs):
        captured.update(kwargs)
        return "summary text"

    monkeypatch.setattr(
        "matmaster.context.compaction.call_summary_llm",
        fake_call_summary_llm,
    )
    provider = Provider("unused")
    compactor = Compactor()
    state = _KernelState(
        messages=[SystemMessage(content="sys"), UserMessage(content="old")]
    )
    kernel_spec = AgentKernelSpec(
        system_prompt="sys",
        max_turns=10,
        compaction=CompactionConfig(
            context_limit=20_000,
            reserved_summary_tokens=1_000,
            summary_safety_margin_tokens=123,
        ),
        run_identity=RunIdentity(),
    )

    [
        item
        async for item in run_compaction_plan(
            kernel_spec=kernel_spec,
            kernel_resources=_kernel_resources(provider, compactor),
            state=state,
            plan=_plan("runtime"),
            checkpoint_sink=None,
            tool_definitions=None,
        )
    ]

    assert captured["safety_margin_tokens"] == 123


@pytest.mark.asyncio
async def test_compaction_plan_runner_preflight_summary_failure_raises() -> None:
    provider = Provider(RuntimeError("network down"))
    compactor = Compactor()
    state = _KernelState(
        messages=[SystemMessage(content="sys"), UserMessage(content="old")]
    )

    with pytest.raises(RuntimeError, match="network down"):
        async for _item in run_compaction_plan(
            kernel_spec=_kernel_spec(),
            kernel_resources=_kernel_resources(provider, compactor),
            state=state,
            plan=_plan("preflight"),
            checkpoint_sink=None,
            tool_definitions=None,
        ):
            pass

    assert compactor.fallback_calls == []


@pytest.mark.asyncio
async def test_compaction_plan_runner_runtime_summary_failure_uses_fallback() -> None:
    provider = Provider(RuntimeError("network down"))
    compactor = Compactor()
    state = _KernelState(
        messages=[SystemMessage(content="sys"), UserMessage(content="old")]
    )

    events = [
        item.event
        async for item in run_compaction_plan(
            kernel_spec=_kernel_spec(),
            kernel_resources=_kernel_resources(provider, compactor),
            state=state,
            plan=_plan("runtime"),
            checkpoint_sink=None,
            tool_definitions=None,
        )
    ]

    assert compactor.summary_calls == []
    assert compactor.fallback_calls[0][1] == "network down"
    assert events[-1].strategy == "sliding_window"


@pytest.mark.asyncio
async def test_compaction_plan_runner_runtime_tool_call_response_uses_fallback() -> (
    None
):
    provider = Provider(
        "summary text",
        tool_calls=[ToolCallData(id="tc-1", name="tool", arguments={})],
    )
    compactor = Compactor()
    state = _KernelState(
        messages=[SystemMessage(content="sys"), UserMessage(content="old")]
    )

    events = [
        item.event
        async for item in run_compaction_plan(
            kernel_spec=_kernel_spec(),
            kernel_resources=_kernel_resources(provider, compactor),
            state=state,
            plan=_plan("runtime"),
            checkpoint_sink=None,
            tool_definitions=[{"type": "function", "function": {"name": "tool"}}],
        )
    ]

    assert compactor.summary_calls == []
    assert compactor.fallback_calls[0][1] == "Summary LLM attempted tool calls"
    assert events[-1].strategy == "sliding_window"
