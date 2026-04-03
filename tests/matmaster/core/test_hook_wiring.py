"""Integration tests for HookExecutor wiring across runtime boundaries."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from matmaster.config.exp import ExpConfig
from matmaster.core.agent import AgentKernel
from matmaster.core.capability_policy import DefaultCapabilityPolicy
from matmaster.core.exp import Exp
from matmaster.core.hooks import (
    CompactionContext,
    HookEvent,
    HookExecutor,
    HookOutcome,
    HookResult,
)
from matmaster.core.structural_validation import StructuralValidation
from matmaster.core.tool_runner import FullToolRunner
from matmaster.core.tool_scheduler import ToolScheduler
from matmaster.tools.tool_catalog import ToolCatalog
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.tools.tool_result import ToolResult
from matmaster.types.context import PlaygroundContext
from matmaster.types.events import ContextCompactionEvent, RunResultEvent
from matmaster.types.messages import (
    AssistantMessage,
    LLMResponse,
    StreamChunk,
    UserMessage,
)
from matmaster.types.topology import ToolPlane
from matmaster.types.runtime import AgentRuntimeSpec
from tests.conftest import MockAsyncTool

from .conftest import MockLLMProvider
from .test_full_tool_runner import _make_ctx, _make_tc, _make_topology


class RecordingProvider:
    def __init__(self) -> None:
        self.seen_messages: list[list[dict[str, object]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None):
        return LLMResponse(content="unused", finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        self.seen_messages.append(messages)
        yield StreamChunk(content="done")
        yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 1})


class FakeCompactor:
    def __init__(self) -> None:
        self._event_sink = None
        self.message_counts: list[int] = []

    def update_message_count(self, count: int) -> None:
        self.message_counts.append(count)

    async def compact_if_needed(self, messages, turn_usage, turn) -> None:
        messages[:] = messages[:1]
        await self._event_sink(
            ContextCompactionEvent(
                source="context_compactor",
                payload={"trigger_tokens": 123, "strategy": "summary"},
            )
        )


class DoubleEventCompactor:
    def __init__(self) -> None:
        self._event_sink = None
        self.message_counts: list[int] = []

    def update_message_count(self, count: int) -> None:
        self.message_counts.append(count)

    async def compact_if_needed(self, messages, turn_usage, turn) -> None:
        messages[:] = messages[:2]
        await self._event_sink(
            ContextCompactionEvent(
                source="context_compactor",
                payload={"trigger_tokens": 111, "strategy": "summary"},
            )
        )
        messages[:] = messages[:1]
        await self._event_sink(
            ContextCompactionEvent(
                source="context_compactor",
                payload={"trigger_tokens": 222, "strategy": "summary"},
            )
        )


def _make_hook_catalog(tool_name: str = "test_tool", result: str = "ok"):
    registry = ToolRegistry()
    tool = MockAsyncTool(name=tool_name, result=result)
    tool.resource_claims = ()
    tool.capabilities = frozenset()
    tool.effect_level = "none"
    tool.fast_path_eligible = True
    tool.max_result_chars = 12000
    tool.plane = ToolPlane.CONTROL_PLANE
    registry.register(tool, source="builtin")
    from matmaster.tools.tool_catalog import ToolCatalog

    return ToolCatalog(registry)


def _make_echo_catalog(tool_name: str = "echo_tool") -> ToolCatalog:
    class EchoTool:
        name = tool_name
        description = "echo tool"
        json_schema = {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }
        resource_claims = ()
        capabilities = frozenset()
        effect_level = "none"
        fast_path_eligible = True
        max_result_chars = 12000
        plane = ToolPlane.CONTROL_PLANE
        state_mode = "stateless"
        stop_mode = "cancellable"
        exposed_to_model = True

        def describe(self, ctx=None):
            return self.description

        def prompt(self, ctx=None):
            return None

        async def execute(self, arguments):
            return ToolResult(content=arguments["value"])

    registry = ToolRegistry()
    registry.register(EchoTool(), source="builtin")
    return ToolCatalog(registry)


class TestExpWiring:
    @pytest.mark.asyncio
    async def test_build_runtime_injects_run_meta_and_runner_hook_executor(
        self,
        tmp_path: Path,
    ) -> None:
        exp = Exp(ExpConfig(name="test"))
        ctx = PlaygroundContext(
            workdir=tmp_path,
            execution_workdir=str(tmp_path / "exec"),
            session_type="local",
            cache_area=tmp_path / "cache",
            run_meta={"task_id": "task-1", "session_id": "session-1"},
            llm_provider=MockLLMProvider(),
        )

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        assert runtime.spec.meta["task_id"] == "task-1"
        assert runtime.spec.meta["session_id"] == "session-1"
        assert runtime.spec.tool_runner._hook_executor is runtime.spec.hook_executor

    @pytest.mark.asyncio
    async def test_make_spawn_fn_emits_subagent_start_and_stop(
        self,
        tmp_path: Path,
    ) -> None:
        started = []
        stopped = []
        executor = HookExecutor()

        async def on_start(ctx) -> None:
            started.append(ctx)

        async def on_stop(ctx) -> None:
            stopped.append(ctx)

        executor.on(HookEvent.SUBAGENT_START, on_start)
        executor.on(HookEvent.SUBAGENT_STOP, on_stop)

        ctx = PlaygroundContext(
            workdir=tmp_path,
            execution_workdir=str(tmp_path / "exec"),
            session_type="local",
            cache_area=tmp_path / "cache",
            run_meta={"session_id": "session-1"},
            llm_provider=MockLLMProvider(),
        )

        async def fake_drain_run_stream(_stream):
            return SimpleNamespace(
                status="completed",
                final_content="child done",
                reason="natural",
            )

        with patch(
            "matmaster.config.loader.load_exp_config",
            return_value=ExpConfig(name="direct"),
        ), patch(
            "matmaster.core.stream_drain.drain_run_stream",
            side_effect=fake_drain_run_stream,
        ):
            spawn_fn = Exp._make_spawn_fn(
                ctx,
                source_prefix="MatMaster",
                hook_executor=executor,
            )
            result = await spawn_fn("direct", "summarize this task")

        assert result == "child done"
        assert len(started) == 1
        assert len(stopped) == 1
        assert started[0].agent_type == "direct"
        assert started[0].parent_session_id == "session-1"
        assert started[0].task_preview == "summarize this task"
        assert stopped[0].agent_id == started[0].agent_id


class TestFullToolRunnerHookWiring:
    @pytest.mark.asyncio
    async def test_pre_tool_call_observes_then_blocks(self) -> None:
        observed: list[tuple[str, int]] = []
        executor = HookExecutor()

        async def observe(ctx) -> None:
            observed.append((ctx.tool_name, ctx.turn))

        async def block(ctx) -> HookResult:
            return HookResult(outcome=HookOutcome.BLOCK, message="denied")

        executor.on(HookEvent.PRE_TOOL_CALL, observe)
        executor.intercept(HookEvent.PRE_TOOL_CALL, block)

        runner = FullToolRunner(
            catalog=_make_hook_catalog("test_tool", result="result_test_tool"),
            structural_validation=StructuralValidation(),
            capability_policy=DefaultCapabilityPolicy(),
            scheduler=ToolScheduler(default_timeout=1.0),
            topology=_make_topology(),
            hook_executor=executor,
        )

        results = await runner.execute_batch([_make_tc("test_tool")], _make_ctx())
        _, result = results[0]

        assert observed == [("test_tool", 1)]
        assert result.status == "blocked"
        assert result.content == "denied"
        assert result.meta["layer"] == "hook"

    @pytest.mark.asyncio
    async def test_pre_tool_call_observer_mutation_does_not_leak(self) -> None:
        intercepted_values: list[str] = []
        executor = HookExecutor()

        async def observe(ctx) -> None:
            ctx.arguments["value"] = "observer-mutated"

        async def allow(ctx) -> HookResult:
            intercepted_values.append(ctx.arguments["value"])
            return HookResult()

        executor.on(HookEvent.PRE_TOOL_CALL, observe)
        executor.intercept(HookEvent.PRE_TOOL_CALL, allow)

        runner = FullToolRunner(
            catalog=_make_echo_catalog(),
            structural_validation=StructuralValidation(),
            capability_policy=DefaultCapabilityPolicy(),
            scheduler=ToolScheduler(default_timeout=1.0),
            topology=_make_topology(),
            hook_executor=executor,
        )

        results = await runner.execute_batch(
            [_make_tc("echo_tool", value="original")],
            _make_ctx(),
        )
        _, result = results[0]

        assert intercepted_values == ["original"]
        assert result.content == "original"

    @pytest.mark.asyncio
    async def test_post_tool_call_rewrites_then_observes(self) -> None:
        observed: list[str] = []
        executor = HookExecutor()

        async def rewrite(ctx, result: ToolResult) -> ToolResult:
            return result.model_copy(update={"content": result.content + " rewritten"})

        async def observe(ctx) -> None:
            observed.append(ctx.result.content)

        executor.rewrite(HookEvent.POST_TOOL_CALL, rewrite)
        executor.on(HookEvent.POST_TOOL_CALL, observe)

        runner = FullToolRunner(
            catalog=_make_hook_catalog("test_tool", result="result_test_tool"),
            structural_validation=StructuralValidation(),
            capability_policy=DefaultCapabilityPolicy(),
            scheduler=ToolScheduler(default_timeout=1.0),
            topology=_make_topology(),
            hook_executor=executor,
        )

        results = await runner.execute_batch([_make_tc("test_tool")], _make_ctx())
        _, result = results[0]

        assert result.content == "result_test_tool rewritten"
        assert observed == ["result_test_tool rewritten"]

    @pytest.mark.asyncio
    async def test_post_tool_call_observer_mutation_does_not_leak(self) -> None:
        executor = HookExecutor()

        async def observe(ctx) -> None:
            ctx.result.content = "observer-mutated"

        executor.on(HookEvent.POST_TOOL_CALL, observe)

        runner = FullToolRunner(
            catalog=_make_hook_catalog("test_tool", result="result_test_tool"),
            structural_validation=StructuralValidation(),
            capability_policy=DefaultCapabilityPolicy(),
            scheduler=ToolScheduler(default_timeout=1.0),
            topology=_make_topology(),
            hook_executor=executor,
        )

        results = await runner.execute_batch([_make_tc("test_tool")], _make_ctx())
        _, result = results[0]

        assert result.content == "result_test_tool"


class TestAgentKernelHookWiring:
    @pytest.mark.asyncio
    async def test_run_stream_emits_run_start_and_run_end(self) -> None:
        provider = RecordingProvider()
        executor = HookExecutor()
        seen: list[tuple[str, str, str, str]] = []

        async def on_start(ctx) -> None:
            seen.append(("start", ctx.task_id, ctx.session_id, ctx.reason))

        async def on_end(ctx) -> None:
            seen.append(("end", ctx.task_id, ctx.session_id, ctx.reason))

        executor.on(HookEvent.RUN_START, on_start)
        executor.on(HookEvent.RUN_END, on_end)

        spec = AgentRuntimeSpec(
            llm_provider=provider,
            hook_executor=executor,
            meta={"task_id": "task-1", "session_id": "session-1"},
            system_prompt="You are a test agent",
        )

        kernel = AgentKernel()
        events = [event async for event in kernel.run_stream(spec, "original")]

        assert isinstance(events[-1], RunResultEvent)
        assert seen == [
            ("start", "task-1", "session-1", "startup"),
            ("end", "task-1", "session-1", "natural"),
        ]

    @pytest.mark.asyncio
    async def test_run_stream_emits_cancelled_run_end_when_closed_early(self) -> None:
        provider = RecordingProvider()
        executor = HookExecutor()
        seen_end_reasons: list[str] = []

        async def on_end(ctx) -> None:
            seen_end_reasons.append(ctx.reason)

        executor.on(HookEvent.RUN_END, on_end)

        spec = AgentRuntimeSpec(
            llm_provider=provider,
            hook_executor=executor,
            meta={"task_id": "task-1", "session_id": "session-1"},
            system_prompt="You are a test agent",
        )

        kernel = AgentKernel()
        stream = kernel.run_stream(spec, "original")

        _first_event = await anext(stream)
        await stream.aclose()

        assert seen_end_reasons == ["cancelled"]

    @pytest.mark.asyncio
    async def test_user_prompt_submit_rewrites_before_provider_call(self) -> None:
        provider = RecordingProvider()
        executor = HookExecutor()
        seen_prompts: list[str] = []

        async def rewrite(ctx, prompt: str) -> str:
            return prompt + " rewritten"

        async def observe(ctx) -> None:
            seen_prompts.append(ctx.prompt)

        executor.rewrite(HookEvent.USER_PROMPT_SUBMIT, rewrite)
        executor.on(HookEvent.USER_PROMPT_SUBMIT, observe)

        spec = AgentRuntimeSpec(
            llm_provider=provider,
            hook_executor=executor,
            meta={"task_id": "task-1", "session_id": "session-1"},
            system_prompt="You are a test agent",
        )

        kernel = AgentKernel()
        _events = [event async for event in kernel.run_stream(spec, "original")]

        assert provider.seen_messages[0][-1]["content"] == "original rewritten"
        assert seen_prompts == ["original rewritten"]

    @pytest.mark.asyncio
    async def test_context_compaction_emits_hook_context(self) -> None:
        provider = RecordingProvider()
        compactor = FakeCompactor()
        executor = HookExecutor()
        seen: list[CompactionContext] = []

        async def observe(ctx: CompactionContext) -> None:
            seen.append(ctx)

        executor.on(HookEvent.CONTEXT_COMPACTION, observe)

        spec = AgentRuntimeSpec(
            llm_provider=provider,
            hook_executor=executor,
            compactor=compactor,
            system_prompt="You are a test agent",
        )

        kernel = AgentKernel()
        _events = [event async for event in kernel.run_stream(spec, "original")]

        assert seen == [
            CompactionContext(
                messages_before=2,
                messages_after=1,
                trigger_tokens=123,
                strategy="summary",
            )
        ]

    @pytest.mark.asyncio
    async def test_context_compaction_tracks_each_event_snapshot(self) -> None:
        provider = RecordingProvider()
        compactor = DoubleEventCompactor()
        executor = HookExecutor()
        seen: list[CompactionContext] = []

        async def observe(ctx: CompactionContext) -> None:
            seen.append(ctx)

        executor.on(HookEvent.CONTEXT_COMPACTION, observe)

        spec = AgentRuntimeSpec(
            llm_provider=provider,
            hook_executor=executor,
            compactor=compactor,
            system_prompt="You are a test agent",
        )

        kernel = AgentKernel()
        _events = [
            event
            async for event in kernel.run_stream(
                spec,
                "original",
                history=[
                    UserMessage(content="previous user"),
                    AssistantMessage(content="previous assistant"),
                ],
            )
        ]

        assert seen == [
            CompactionContext(
                messages_before=4,
                messages_after=2,
                trigger_tokens=111,
                strategy="summary",
            ),
            CompactionContext(
                messages_before=2,
                messages_after=1,
                trigger_tokens=222,
                strategy="summary",
            ),
        ]
