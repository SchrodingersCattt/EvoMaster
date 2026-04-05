"""Tests for the redesigned hook system."""

from __future__ import annotations

import pytest

from matmaster.core.hooks import (
    CompactionContext,
    HookEvent,
    HookExecutor,
    HookOutcome,
    HookResult,
    PostToolCallContext,
    PreToolCallContext,
    RunContext,
    SubagentContext,
    UserPromptContext,
)
from matmaster.tools.tool_result import ToolResult


class TestHookEvent:
    def test_all_events_defined(self) -> None:
        assert len(HookEvent) == 8

    def test_values_are_strings(self) -> None:
        assert HookEvent.RUN_START == "run_start"
        assert HookEvent.RUN_END == "run_end"
        assert HookEvent.PRE_TOOL_CALL == "pre_tool_call"
        assert HookEvent.POST_TOOL_CALL == "post_tool_call"
        assert HookEvent.SUBAGENT_START == "subagent_start"
        assert HookEvent.SUBAGENT_STOP == "subagent_stop"
        assert HookEvent.CONTEXT_COMPACTION == "context_compaction"
        assert HookEvent.USER_PROMPT_SUBMIT == "user_prompt_submit"


class TestHookOutcome:
    def test_outcomes(self) -> None:
        assert HookOutcome.SUCCESS == "success"
        assert HookOutcome.BLOCK == "block"
        assert HookOutcome.ERROR == "error"


class TestHookResult:
    def test_defaults(self) -> None:
        result = HookResult()
        assert result.outcome == HookOutcome.SUCCESS
        assert result.message == ""
        assert result.data is None

    def test_block_with_message(self) -> None:
        result = HookResult(outcome=HookOutcome.BLOCK, message="blocked")
        assert result.outcome == HookOutcome.BLOCK
        assert result.message == "blocked"


class TestContextDataclasses:
    def test_run_context_frozen(self) -> None:
        ctx = RunContext(task_id="t1", session_id="s1", reason="startup")
        with pytest.raises(AttributeError):
            ctx.reason = "other"  # type: ignore[misc]

    def test_pre_tool_call_context(self) -> None:
        ctx = PreToolCallContext(
            tool_name="bash",
            tool_call_id="tc1",
            arguments={"cmd": "ls"},
            turn=1,
        )
        assert ctx.tool_name == "bash"
        assert ctx.turn == 1

    def test_post_tool_call_context(self) -> None:
        result = ToolResult(status="success", content="ok")
        ctx = PostToolCallContext(
            tool_name="bash",
            tool_call_id="tc1",
            arguments={},
            result=result,
            turn=2,
        )
        assert ctx.result.status == "success"

    def test_subagent_context_default_task_preview(self) -> None:
        ctx = SubagentContext(
            agent_id="a1",
            agent_type="direct",
            parent_session_id="s1",
        )
        assert ctx.task_preview == ""

    def test_compaction_context(self) -> None:
        ctx = CompactionContext(
            messages_before=100,
            messages_after=20,
            trigger_tokens=8000,
            strategy="summary",
        )
        assert ctx.trigger_tokens == 8000

    def test_user_prompt_context(self) -> None:
        ctx = UserPromptContext(prompt="hello", session_id="s1")
        assert ctx.prompt == "hello"


class TestHookExecutorEmit:
    async def test_emit_no_handlers(self) -> None:
        executor = HookExecutor()
        await executor.emit(HookEvent.RUN_START, RunContext("t1", "s1", "startup"))

    async def test_emit_calls_all_observers(self) -> None:
        executor = HookExecutor()
        calls: list[tuple[str, str]] = []

        async def obs1(ctx: RunContext) -> None:
            calls.append(("obs1", ctx.reason))

        async def obs2(ctx: RunContext) -> None:
            calls.append(("obs2", ctx.reason))

        executor.on(HookEvent.RUN_START, obs1)
        executor.on(HookEvent.RUN_START, obs2)

        await executor.emit(HookEvent.RUN_START, RunContext("t1", "s1", "startup"))

        assert ("obs1", "startup") in calls
        assert ("obs2", "startup") in calls

    async def test_emit_swallows_exceptions(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        executor = HookExecutor()
        caplog.set_level("WARNING")
        good_called = False

        async def bad_hook(ctx: RunContext) -> None:
            raise ValueError("boom")

        async def good_hook(ctx: RunContext) -> None:
            nonlocal good_called
            good_called = True

        executor.on(HookEvent.RUN_START, bad_hook)
        executor.on(HookEvent.RUN_START, good_hook)

        await executor.emit(HookEvent.RUN_START, RunContext("t1", "s1", "startup"))

        assert good_called is True
        assert "boom" in caplog.text

    async def test_emit_ignores_other_events(self) -> None:
        executor = HookExecutor()
        called = False

        async def obs(ctx: RunContext) -> None:
            nonlocal called
            called = True

        executor.on(HookEvent.RUN_START, obs)
        await executor.emit(HookEvent.RUN_END, RunContext("t1", "s1", "completed"))
        assert called is False


class TestHookExecutorIntercept:
    async def test_intercept_no_handlers_returns_success(self) -> None:
        executor = HookExecutor()
        result = await executor.emit_intercept(
            HookEvent.PRE_TOOL_CALL,
            PreToolCallContext("bash", "tc1", {}, 1),
        )
        assert result.outcome == HookOutcome.SUCCESS

    async def test_intercept_single_block(self) -> None:
        executor = HookExecutor()

        async def blocker(ctx: PreToolCallContext) -> HookResult:
            return HookResult(outcome=HookOutcome.BLOCK, message="denied")

        executor.intercept(HookEvent.PRE_TOOL_CALL, blocker)
        result = await executor.emit_intercept(
            HookEvent.PRE_TOOL_CALL,
            PreToolCallContext("bash", "tc1", {}, 1),
        )

        assert result.outcome == HookOutcome.BLOCK
        assert result.message == "denied"

    async def test_intercept_multiple_blocks_aggregate_messages(self) -> None:
        executor = HookExecutor()

        async def blocker1(ctx: PreToolCallContext) -> HookResult:
            return HookResult(outcome=HookOutcome.BLOCK, message="reason1")

        async def blocker2(ctx: PreToolCallContext) -> HookResult:
            return HookResult(outcome=HookOutcome.BLOCK, message="reason2")

        executor.intercept(HookEvent.PRE_TOOL_CALL, blocker1)
        executor.intercept(HookEvent.PRE_TOOL_CALL, blocker2)
        result = await executor.emit_intercept(
            HookEvent.PRE_TOOL_CALL,
            PreToolCallContext("bash", "tc1", {}, 1),
        )

        assert result.outcome == HookOutcome.BLOCK
        assert "reason1" in result.message
        assert "reason2" in result.message

    async def test_intercept_all_success(self) -> None:
        executor = HookExecutor()

        async def allow(ctx: PreToolCallContext) -> HookResult:
            return HookResult()

        executor.intercept(HookEvent.PRE_TOOL_CALL, allow)
        result = await executor.emit_intercept(
            HookEvent.PRE_TOOL_CALL,
            PreToolCallContext("bash", "tc1", {}, 1),
        )

        assert result.outcome == HookOutcome.SUCCESS

    async def test_intercept_exception_becomes_error(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        executor = HookExecutor()
        caplog.set_level("WARNING")

        async def bad(ctx: PreToolCallContext) -> HookResult:
            raise RuntimeError("oops")

        executor.intercept(HookEvent.PRE_TOOL_CALL, bad)
        result = await executor.emit_intercept(
            HookEvent.PRE_TOOL_CALL,
            PreToolCallContext("bash", "tc1", {}, 1),
        )

        assert result.outcome == HookOutcome.SUCCESS
        assert "oops" in caplog.text


class TestHookExecutorRewrite:
    async def test_rewrite_no_handlers_returns_original(self) -> None:
        executor = HookExecutor()
        result = await executor.emit_rewrite(
            HookEvent.USER_PROMPT_SUBMIT,
            UserPromptContext("hello", "s1"),
            "hello",
        )
        assert result == "hello"

    async def test_rewrite_single_modifier(self) -> None:
        executor = HookExecutor()

        async def add_prefix(ctx: UserPromptContext, prompt: str) -> str:
            return f"[modified] {prompt}"

        executor.rewrite(HookEvent.USER_PROMPT_SUBMIT, add_prefix)
        result = await executor.emit_rewrite(
            HookEvent.USER_PROMPT_SUBMIT,
            UserPromptContext("hello", "s1"),
            "hello",
        )

        assert result == "[modified] hello"

    async def test_rewrite_chain_passes_previous_output(self) -> None:
        executor = HookExecutor()

        async def step1(ctx: UserPromptContext, data: str) -> str:
            return f"({data})"

        async def step2(ctx: UserPromptContext, data: str) -> str:
            return f"[{data}]"

        executor.rewrite(HookEvent.USER_PROMPT_SUBMIT, step1)
        executor.rewrite(HookEvent.USER_PROMPT_SUBMIT, step2)
        result = await executor.emit_rewrite(
            HookEvent.USER_PROMPT_SUBMIT,
            UserPromptContext("x", "s1"),
            "x",
        )

        assert result == "[(x)]"

    async def test_rewrite_none_means_no_change(self) -> None:
        executor = HookExecutor()

        async def noop(ctx: UserPromptContext, data: str) -> None:
            return None

        async def modify(ctx: UserPromptContext, data: str) -> str:
            return f"[{data}]"

        executor.rewrite(HookEvent.USER_PROMPT_SUBMIT, noop)
        executor.rewrite(HookEvent.USER_PROMPT_SUBMIT, modify)
        result = await executor.emit_rewrite(
            HookEvent.USER_PROMPT_SUBMIT,
            UserPromptContext("x", "s1"),
            "x",
        )

        assert result == "[x]"

    async def test_rewrite_none_discards_in_place_mutation(self) -> None:
        executor = HookExecutor()
        original = ToolResult(content="original")

        async def mutate_but_return_none(
            ctx: PostToolCallContext, data: ToolResult
        ) -> None:
            data.content = "mutated"
            return None

        executor.rewrite(HookEvent.POST_TOOL_CALL, mutate_but_return_none)
        result = await executor.emit_rewrite(
            HookEvent.POST_TOOL_CALL,
            PostToolCallContext(
                tool_name="bash",
                tool_call_id="tc1",
                arguments={},
                result=original,
                turn=1,
            ),
            original,
        )

        assert result.content == "original"

    async def test_rewrite_exception_swallowed(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        executor = HookExecutor()
        caplog.set_level("WARNING")

        async def bad(ctx: UserPromptContext, data: str) -> str:
            raise ValueError("fail")

        async def good(ctx: UserPromptContext, data: str) -> str:
            return f"[{data}]"

        executor.rewrite(HookEvent.USER_PROMPT_SUBMIT, bad)
        executor.rewrite(HookEvent.USER_PROMPT_SUBMIT, good)
        result = await executor.emit_rewrite(
            HookEvent.USER_PROMPT_SUBMIT,
            UserPromptContext("x", "s1"),
            "x",
        )

        assert result == "[x]"
        assert "fail" in caplog.text
