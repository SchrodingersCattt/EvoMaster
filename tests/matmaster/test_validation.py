"""Tests for matmaster.validation -- async Protocol validation helper."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from matmaster.tools.tool_registry import Tool
from matmaster.types.llm_provider import LLMProvider
from matmaster.types.messages import LLMResponse, StreamChunk
from matmaster.types.topology import ToolPlane
from matmaster.validation import validate_async_protocol

# -- Sync mocks (deliberately wrong for async Protocol) --


class SyncLLMProvider:
    def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="sync", finish_reason="stop")

    def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="sync", finish_reason="stop")


class SyncTool:
    resource_claims = ()
    capabilities = frozenset()
    effect_level = "local_mutation"
    fast_path_eligible = False
    max_result_chars = 0
    plane = ToolPlane.CONTROL_PLANE
    state_mode = "stateless"
    stop_mode = "cancellable"
    exposed_to_model = True

    @property
    def name(self) -> str:
        return "sync_tool"

    @property
    def description(self) -> str:
        return "sync"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {}

    def describe(self, ctx: Any) -> str:
        return self.description

    def prompt(self, ctx: Any | None = None) -> str | None:
        return None

    def execute(self, arguments: dict[str, Any]) -> str:
        return "sync"


# -- Async mocks (correct for async Protocol) --


class AsyncLLMProviderOK:
    async def __aenter__(self) -> AsyncLLMProviderOK:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="async", finish_reason="stop")

    async def chat_stream(
        self, messages, tools=None, *, timeout=None
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content="async", finish_reason="stop")


class AsyncLLMProviderCoroutineOnly:
    """Both methods are coroutine functions (no yield). Also valid."""

    async def __aenter__(self) -> AsyncLLMProviderCoroutineOnly:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="async", finish_reason="stop")

    async def chat_stream(
        self, messages, tools=None, *, timeout=None
    ) -> AsyncIterator[StreamChunk]:
        return  # type: ignore[return-value]


class AsyncToolOK:
    resource_claims = ()
    capabilities = frozenset()
    effect_level = "local_mutation"
    fast_path_eligible = False
    max_result_chars = 0
    plane = ToolPlane.CONTROL_PLANE
    state_mode = "stateless"
    stop_mode = "cancellable"
    exposed_to_model = True

    @property
    def name(self) -> str:
        return "async_tool"

    @property
    def description(self) -> str:
        return "async"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {}

    def describe(self, ctx: Any) -> str:
        return self.description

    def prompt(self, ctx: Any | None = None) -> str | None:
        return None

    async def execute(self, arguments: dict[str, Any]) -> str:
        return "async"


# -- Tests --


class TestValidateAsyncProtocol:
    def test_detects_sync_mismatch_on_llm_provider(self) -> None:
        """Sync LLMProvider implementation fails async Protocol validation."""
        errors = validate_async_protocol(SyncLLMProvider(), LLMProvider)
        assert len(errors) == 4
        assert any("__aenter__" in e and "missing method" in e for e in errors)
        assert any("__aexit__" in e and "missing method" in e for e in errors)
        assert any("chat()" in e and "expected async def" in e for e in errors)
        assert any("chat_stream()" in e and "expected async def" in e for e in errors)

    def test_passes_async_llm_provider_with_async_generator(self) -> None:
        """Async LLMProvider with async generator chat_stream passes validation."""
        errors = validate_async_protocol(AsyncLLMProviderOK(), LLMProvider)
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_passes_async_llm_provider_coroutine_only(self) -> None:
        """Async LLMProvider with coroutine-only chat_stream also passes."""
        errors = validate_async_protocol(AsyncLLMProviderCoroutineOnly(), LLMProvider)
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

    def test_missing_method(self) -> None:
        class Empty:
            pass

        errors = validate_async_protocol(Empty(), LLMProvider)
        assert len(errors) == 4
        assert any("missing method" in e for e in errors)


class TestAsyncGeneratorDetection:
    """Specifically test async generator vs coroutine function handling."""

    def test_async_generator_chat_stream_passes(self) -> None:
        """chat_stream as async generator (yield) passes validation for async Protocol stub."""
        import inspect

        provider = AsyncLLMProviderOK()
        # Verify our test mock is actually an async generator
        assert inspect.isasyncgenfunction(
            provider.chat_stream
        ), "Test setup: chat_stream should be async generator"
        assert not inspect.iscoroutinefunction(
            provider.chat_stream
        ), "Test setup: async generator should NOT be iscoroutinefunction"
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


class TestValidationWithConftest:
    def test_mock_async_provider_passes_validation(self) -> None:
        from tests.conftest import MockAsyncLLMProvider

        errors = validate_async_protocol(MockAsyncLLMProvider(), LLMProvider)
        assert errors == []

    def test_mock_async_tool_passes_validation(self) -> None:
        from tests.conftest import MockAsyncTool

        errors = validate_async_protocol(MockAsyncTool(), Tool)
        assert errors == []
