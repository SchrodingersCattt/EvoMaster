"""E3 R2 guards for the ToolCallData.arguments immutability contract."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from matmaster.core.capability_policy import DefaultCapabilityPolicy
from matmaster.core.structural_validation import StructuralValidation
from matmaster.core.tool_runner import FullToolRunner, ToolExecutionContext
from matmaster.core.tool_scheduler import ToolScheduler
from matmaster.tools.tool_catalog import ToolCatalog
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.tools.tool_result import ToolResult
from matmaster.types.messages import ToolCallData
from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import RuntimeTopology, ToolPlane


class _RecordingTool:
    """Minimal non-mutating tool that records the arguments object it receives."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.resource_claims: tuple[ResourceClaim, ...] = ()
        self.capabilities = frozenset()
        self.effect_level = "none"
        self.fast_path_eligible = True
        self.max_result_chars = 0
        self.plane = ToolPlane.CONTROL_PLANE
        self.state_mode = "stateless"
        self.stop_mode = "cancellable"
        self.exposed_to_model = True

    @property
    def name(self) -> str:
        return "record_args"

    @property
    def description(self) -> str:
        return "record arguments for contract testing"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def describe(self, ctx: Any | None = None) -> str:
        return self.description

    def prompt(self, ctx: Any | None = None) -> str | None:
        return None

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append(arguments)
        return ToolResult(content="ok")


def test_nested_mutation_stales_arguments_json_cache():
    """Nested mutation is allowed by frozen, and that would stale the cache."""
    tc = ToolCallData(id="c1", name="synthetic_mut", arguments={"q": "hello", "n": 5})

    cached_before = tc.arguments_json
    assert json.loads(cached_before) == {"q": "hello", "n": 5}

    with pytest.raises(Exception):
        tc.arguments = {"q": "rebind"}  # type: ignore[misc]

    tc.arguments["q"] = "MUTATED"
    assert tc.arguments["q"] == "MUTATED"

    cached_after = tc.arguments_json
    assert cached_before == cached_after
    assert "MUTATED" not in cached_after


@pytest.mark.asyncio
async def test_full_tool_runner_chain_does_not_mutate_arguments():
    """The FullToolRunner validation/policy/execution chain leaves args untouched."""
    tool = _RecordingTool()
    registry = ToolRegistry()
    registry.register(tool, source="builtin")
    topology = RuntimeTopology(
        session_kind="local",
        control_root="/tmp/control",
        workspace_root="/tmp/workspace",
        active_planes=frozenset(ToolPlane),
    )
    catalog = ToolCatalog(registry, topology=topology)
    runner = FullToolRunner(
        catalog=catalog,
        structural_validation=StructuralValidation(),
        capability_policy=DefaultCapabilityPolicy(),
        scheduler=ToolScheduler(default_timeout=1.0),
        topology=topology,
    )

    tc = ToolCallData(
        id="c1",
        name=tool.name,
        arguments={"q": "hello", "nested": {"n": 5}},
    )
    snapshot = copy.deepcopy(tc.arguments)
    cached_before = tc.arguments_json

    results = await runner.execute_batch([tc], ToolExecutionContext(turn=1, max_turns=3))

    assert len(results) == 1
    assert results[0][1].status == "success"
    assert tool.calls == [tc.arguments]
    assert tc.arguments == snapshot
    assert tc.arguments_json == cached_before
