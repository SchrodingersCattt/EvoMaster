"""Tests for matmaster.validation -- async Protocol validation helper."""

from __future__ import annotations

from typing import Any, AsyncIterator

from matmaster.core.hooks import Hook, HookAction
from matmaster.tools.tool_result import ToolResult
from matmaster.types.guards import Guard, GuardContext, GuardResult
from matmaster.types.llm_provider import LLMProvider
from matmaster.types.messages import LLMResponse, Message, StreamChunk, ToolCallData
from matmaster.tools.tool_registry import Tool
from matmaster.validation import validate_async_protocol


# -- Sync mocks (deliberately wrong for async Protocol) --


class SyncLLMProvider:
    def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="sync", finish_reason="stop")

    def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="sync", finish_reason="stop")


class SyncTool:
    @property
    def name(self) -> str:
        return "sync_tool"

    @property
    def description(self) -> str:
        return "sync"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {}

    def execute(self, arguments: dict[str, Any]) -> str:
        return "sync"


class SyncHook:
    def pre_tool_call(self, tool_call):
        return HookAction.CONTINUE

    def post_tool_call(self, tool_call, result):
        pass

    def pre_llm_call(self, messages, turn):
        pass

    def should_continue(self, messages, turn):
        return True

    def on_stream_chunk(self, chunk):
        pass

    def on_segment_complete(self, segment_type, content, stream_id):
        pass

    def on_guard_blocked(self, tool_call, result):
        pass


# -- Async mocks (correct for async Protocol) --


class AsyncLLMProviderOK:
    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="async", finish_reason="stop")

    async def chat_stream(
        self, messages, tools=None, *, timeout=None
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content="async", finish_reason="stop")


class AsyncLLMProviderCoroutineOnly:
    """Both methods are coroutine functions (no yield). Also valid."""

    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="async", finish_reason="stop")

    async def chat_stream(
        self, messages, tools=None, *, timeout=None
    ) -> AsyncIterator[StreamChunk]:
        return  # type: ignore[return-value]


class AsyncToolOK:
    @property
    def name(self) -> str:
        return "async_tool"

    @property
    def description(self) -> str:
        return "async"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {}

    async def execute(self, arguments: dict[str, Any]) -> str:
        return "async"


class AsyncHookOK:
    async def pre_tool_call(self, tool_call):
        return HookAction.CONTINUE

    async def post_tool_call(self, tool_call, result):
        pass

    async def pre_llm_call(self, messages, turn):
        pass

    async def should_continue(self, messages, turn):
        return True

    async def on_stream_chunk(self, chunk):
        pass

    async def on_segment_complete(self, segment_type, content, stream_id):
        pass

    async def on_guard_blocked(self, tool_call, result):
        pass


class SyncGuardOK:
    def evaluate(self, ctx: GuardContext) -> GuardResult:
        return GuardResult(allowed=True)


class AsyncGuardWrong:
    async def evaluate(self, ctx: GuardContext) -> GuardResult:
        return GuardResult(allowed=True)


# -- Tests --


class TestValidateAsyncProtocol:
    def test_detects_sync_mismatch_on_llm_provider(self) -> None:
        """Sync LLMProvider implementation fails async Protocol validation."""
        errors = validate_async_protocol(SyncLLMProvider(), LLMProvider)
        assert len(errors) == 2
        assert any("chat()" in e and "expected async def" in e for e in errors)
        assert any("chat_stream()" in e and "expected async def" in e for e in errors)

    def test_passes_async_llm_provider_with_async_generator(self) -> None:
        """Async LLMProvider with async generator chat_stream passes validation."""
        errors = validate_async_protocol(AsyncLLMProviderOK(), LLMProvider)
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_passes_async_llm_provider_coroutine_only(self) -> None:
        """Async LLMProvider with coroutine-only chat_stream also passes."""
        errors = validate_async_protocol(
            AsyncLLMProviderCoroutineOnly(), LLMProvider
        )
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_detects_sync_mismatch_on_tool(self) -> None:
        """Sync Tool.execute fails async Protocol validation. Properties skipped."""
        errors = validate_async_protocol(SyncTool(), Tool)
        assert len(errors) == 1
        assert "execute()" in errors[0]
        assert "expected async def" in errors[0]

    def test_passes_async_tool(self) -> None:
        errors = validate_async_protocol(AsyncToolOK(), Tool)
        assert errors == []

    def test_detects_sync_mismatch_on_hook(self) -> None:
        """Sync Hook fails -- all 7 methods should report mismatch."""
        errors = validate_async_protocol(SyncHook(), Hook)
        assert len(errors) == 7

    def test_passes_async_hook(self) -> None:
        errors = validate_async_protocol(AsyncHookOK(), Hook)
        assert errors == []

    def test_guard_sync_passes(self) -> None:
        errors = validate_async_protocol(SyncGuardOK(), Guard)
        assert errors == []

    def test_guard_async_fails(self) -> None:
        errors = validate_async_protocol(AsyncGuardWrong(), Guard)
        assert len(errors) == 1
        assert "evaluate()" in errors[0]
        assert "expected def" in errors[0]

    def test_missing_method(self) -> None:
        class Empty:
            pass

        errors = validate_async_protocol(Empty(), LLMProvider)
        assert len(errors) == 2
        assert any("missing method" in e for e in errors)


class TestAsyncGeneratorDetection:
    """Specifically test async generator vs coroutine function handling."""

    def test_async_generator_chat_stream_passes(self) -> None:
        """chat_stream as async generator (yield) passes validation for async Protocol stub."""
        import inspect

        provider = AsyncLLMProviderOK()
        # Verify our test mock is actually an async generator
        assert inspect.isasyncgenfunction(provider.chat_stream), (
            "Test setup: chat_stream should be async generator"
        )
        assert not inspect.iscoroutinefunction(provider.chat_stream), (
            "Test setup: async generator should NOT be iscoroutinefunction"
        )
        # But validation should still pass
        errors = validate_async_protocol(provider, LLMProvider)
        assert errors == [], f"Async generator should be accepted as async: {errors}"

    def test_sync_generator_chat_stream_fails(self) -> None:
        """Sync generator (def + yield, not async def + yield) should fail."""
        import inspect

        provider = SyncLLMProvider()
        assert not inspect.isasyncgenfunction(provider.chat_stream)
        assert not inspect.iscoroutinefunction(provider.chat_stream)
        errors = validate_async_protocol(provider, LLMProvider)
        assert any("chat_stream()" in e for e in errors)


class TestAsyncTestInfrastructure:
    """Verify pytest-asyncio auto mode works with async def test."""

    async def test_async_test_runs(self) -> None:
        result = 1 + 1
        assert result == 2

    async def test_async_mock_provider_works(self) -> None:
        from tests.conftest import MockAsyncLLMProvider

        provider = MockAsyncLLMProvider()
        response = await provider.chat([{"role": "user", "content": "hi"}])
        assert response.content == "mock response"

    async def test_async_mock_tool_works(self) -> None:
        from tests.conftest import MockAsyncTool

        tool = MockAsyncTool()
        result = await tool.execute({"key": "value"})
        assert result == "ok"

    async def test_async_mock_hook_works(self) -> None:
        from tests.conftest import MockAsyncHook

        hook = MockAsyncHook()
        action = await hook.pre_tool_call(
            ToolCallData(id="tc-1", name="test", arguments={})
        )
        assert action == HookAction.CONTINUE


class TestValidationWithConftest:
    def test_mock_async_provider_passes_validation(self) -> None:
        from tests.conftest import MockAsyncLLMProvider

        errors = validate_async_protocol(MockAsyncLLMProvider(), LLMProvider)
        assert errors == []

    def test_mock_async_tool_passes_validation(self) -> None:
        from tests.conftest import MockAsyncTool

        errors = validate_async_protocol(MockAsyncTool(), Tool)
        assert errors == []

    def test_mock_async_hook_passes_validation(self) -> None:
        from tests.conftest import MockAsyncHook

        errors = validate_async_protocol(MockAsyncHook(), Hook)
        assert errors == []
