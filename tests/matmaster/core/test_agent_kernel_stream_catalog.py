"""Catalog invalidation and cancellation tests for AgentKernel streams."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, PropertyMock

import pytest

from matmaster.core.agent import AgentKernel
from matmaster.core.agent_llm_stream import _sleep_backoff_with_cancel
from matmaster.core.kernel_items import _KernelStopRequested
from matmaster.types.cancellation import CancellationController
from matmaster.types.events import RunResultEvent
from matmaster.types.tool_desc_ctx import ToolDescriptionContext

from .agent_kernel_test_helpers import _make_tool_registry, make_kernel_runtime
from .test_agent_kernel_stream import ContentOnlyProvider, ToolCallStreamProvider


class TestGap3CatalogVersionInvalidation:
    """Gap 3: catalog.version change invalidates cached tool_definitions."""

    @pytest.mark.asyncio
    async def test_catalog_version_invalidates_tool_definitions(self) -> None:
        """When catalog.version changes, _run_items rebuilds tool_definitions."""
        provider = ContentOnlyProvider()
        registry, _ = _make_tool_registry()

        mock_catalog = MagicMock()
        type(mock_catalog).version = PropertyMock(return_value=1)
        mock_catalog.build_definitions = MagicMock(
            return_value=[
                {"type": "function", "function": {"name": "test", "parameters": {}}}
            ]
        )

        kernel_runtime = make_kernel_runtime(
            provider=provider, tool_registry=registry, tool_catalog=mock_catalog
        )

        kernel = AgentKernel()
        events = []
        async for event in kernel.run_stream(kernel_runtime, "test task"):
            events.append(event)

        assert (
            mock_catalog.build_definitions.called
        ), "tool_catalog.build_definitions() should be called when catalog is present"
        args, _ = mock_catalog.build_definitions.call_args
        assert isinstance(args[0], ToolDescriptionContext)

    @pytest.mark.asyncio
    async def test_catalog_version_no_refresh_when_unchanged(self) -> None:
        """When catalog.version is unchanged across turns, no extra build_definitions call."""
        provider = ToolCallStreamProvider()
        registry, _ = _make_tool_registry()

        mock_catalog = MagicMock()
        type(mock_catalog).version = PropertyMock(return_value=1)
        mock_catalog.build_definitions = MagicMock(
            return_value=[
                {
                    "type": "function",
                    "function": {"name": "test_tool", "parameters": {}},
                }
            ]
        )

        kernel_runtime = make_kernel_runtime(
            provider=provider, tool_registry=registry, tool_catalog=mock_catalog
        )

        kernel = AgentKernel()
        events = []
        async for event in kernel.run_stream(kernel_runtime, "test task"):
            events.append(event)

        build_calls = mock_catalog.build_definitions.call_count
        assert (
            build_calls == 1
        ), f"build_definitions should be called once (caching), got {build_calls}"
        args, _ = mock_catalog.build_definitions.call_args
        assert isinstance(args[0], ToolDescriptionContext)


class TestCancellationTokenSupport:
    @pytest.mark.asyncio
    async def test_run_stream_returns_cancelled_when_token_already_cancelled(
        self,
    ) -> None:
        provider = ContentOnlyProvider()
        kernel_runtime = make_kernel_runtime(provider=provider)
        kernel = AgentKernel()
        ctrl = CancellationController()
        ctrl.cancel()

        events: list[object] = []
        async for event in kernel.run_stream(
            kernel_runtime, "test task", cancel_token=ctrl.token
        ):
            events.append(event)

        assert isinstance(events[-1], RunResultEvent)
        assert events[-1].status == "cancelled"
        assert events[-1].reason == "cancelled"

    @pytest.mark.asyncio
    async def test_sleep_backoff_wakes_early_on_cancel_token(self) -> None:
        ctrl = CancellationController()
        task = asyncio.create_task(_sleep_backoff_with_cancel(5.0, ctrl.token))

        await asyncio.sleep(0.05)
        ctrl.cancel()

        with pytest.raises(_KernelStopRequested):
            await task
