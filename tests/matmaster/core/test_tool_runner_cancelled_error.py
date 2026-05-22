"""Test that asyncio.CancelledError raised inside a tool executor is caught
and converted to ToolResult(status='cancelled') instead of propagating as
a BaseException — which would bypass all except-Exception handlers upstream
and prevent CancelledEvent/StreamClosedEvent from reaching the frontend.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from matmaster.core.capability_policy import DefaultCapabilityPolicy
from matmaster.core.structural_validation import StructuralValidation
from matmaster.core.tool_runner import FullToolRunner, ToolExecutionContext
from matmaster.core.tool_scheduler import ToolScheduler
from matmaster.tools.tool_result import ToolResult
from matmaster.types.messages import ToolCallData
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolBinding, ToolInstance, ToolSpec
from matmaster.types.topology import RuntimeTopology, ToolPlane


def _make_topology() -> RuntimeTopology:
    return RuntimeTopology(
        session_kind="local",
        control_root="/tmp/ctrl",
        workspace_root="/tmp/ws",
        active_planes=frozenset(ToolPlane),
    )


def _make_tc(name: str, call_id: str = "") -> ToolCallData:
    return ToolCallData(id=call_id or f"call_{name}", name=name, arguments={})


class TestCancelledErrorDuringExecution:
    @pytest.mark.asyncio
    async def test_cancelled_error_yields_cancelled_result(self) -> None:
        """Tool executor raising CancelledError -> ToolResult(status='cancelled')."""

        async def _raise_cancelled(args: Any, ctx: Any) -> ToolResult:
            raise asyncio.CancelledError("cancelled by user")

        spec = ToolSpec(
            tool_name="blocking_tool",
            effect_level="none",
            fast_path_eligible=True,
        )
        binding = ToolBinding(
            binding_key="control:blocking_tool",
            plane=ToolPlane.CONTROL_PLANE,
            resource_claims=(),
            state_mode="stateless",
            stop_mode="cancellable",
        )
        instance = ToolInstance(
            tool_spec=spec,
            tool_binding=binding,
            tool_executor=_raise_cancelled,
            input_validator=None,
        )

        catalog = MagicMock()
        catalog.get_tool.return_value = instance

        runner = FullToolRunner(
            catalog=catalog,
            structural_validation=StructuralValidation(),
            capability_policy=DefaultCapabilityPolicy(),
            scheduler=ToolScheduler(default_timeout=1.0),
            topology=_make_topology(),
            state=ToolRunnerState(),
        )
        ctx = ToolExecutionContext(turn=1, max_turns=10)

        results = await runner.execute_batch([_make_tc("blocking_tool")], ctx)

        assert len(results) == 1
        _, tr = results[0]
        assert tr.status == "cancelled"
        assert "cancelled" in tr.content.lower()
