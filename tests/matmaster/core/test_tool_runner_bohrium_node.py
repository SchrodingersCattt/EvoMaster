from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from matmaster.core.capability_policy import DefaultCapabilityPolicy
from matmaster.core.structural_validation import StructuralValidation
from matmaster.core.tool_runner import FullToolRunner, ToolExecutionContext
from matmaster.core.tool_scheduler import ToolScheduler
from matmaster.tools.tool_catalog import ToolCatalog
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.tools.tool_result import ToolResult
from matmaster.types.bohrium_node_runtime import BohriumNodeUnavailableError
from matmaster.types.messages import ToolCallData
from matmaster.types.topology import RuntimeTopology, ToolPlane


class _SessionTool:
    name = "remote_read"
    description = "read remotely"
    json_schema = {"type": "object", "properties": {}}
    resource_claims = ()
    capabilities = frozenset()
    effect_level = "none"
    fast_path_eligible = False
    max_result_chars = 0
    plane = ToolPlane.SESSION_FS
    state_mode = "stateless"
    stop_mode = "cancellable"
    exposed_to_model = True

    def describe(self, ctx: Any | None = None) -> str:
        return self.description

    def prompt(self, ctx: Any | None = None) -> None:
        return None

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(status="success", content="read-ok")


class _BohriumControlTool:
    name = "bohrium_control"
    description = "control Bohrium jobs"
    json_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["submit", "query", "download"],
            }
        },
        "required": ["action"],
    }
    resource_claims = ()
    capabilities = frozenset({"bohrium.submit", "bohrium.query", "bohrium.download"})
    effect_level = "external_effect"
    fast_path_eligible = False
    max_result_chars = 0
    plane = ToolPlane.EXTERNAL_SERVICE
    state_mode = "stateless"
    stop_mode = "cancellable"
    exposed_to_model = True

    def describe(self, ctx: Any | None = None) -> str:
        return self.description

    def prompt(self, ctx: Any | None = None) -> None:
        return None

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(status="success", content=str(arguments["action"]))


def _runner(acquirer: Any, tool: Any | None = None) -> FullToolRunner:
    registry = ToolRegistry()
    registry.register(tool or _SessionTool(), source="builtin")
    topology = RuntimeTopology(
        session_kind="bohrium-deferred",
        control_root="/tmp/control",
        workspace_root="/share/case",
        active_planes=frozenset(ToolPlane),
    )
    return FullToolRunner(
        catalog=ToolCatalog(registry),
        structural_validation=StructuralValidation(),
        capability_policy=DefaultCapabilityPolicy(),
        scheduler=ToolScheduler(default_timeout=1),
        topology=topology,
        bohrium_node_acquirer=acquirer,
    )


@pytest.mark.asyncio
async def test_session_tool_acquires_node_before_execution() -> None:
    acquirer = MagicMock()
    acquirer.ensure_ready = AsyncMock(return_value=MagicMock())

    results = await _runner(acquirer).execute_batch(
        [ToolCallData(id="call-1", name="remote_read", arguments={})],
        ToolExecutionContext(turn=1, max_turns=10),
    )

    acquirer.ensure_ready.assert_awaited_once()
    assert results[0][1].status == "success"
    assert results[0][1].content == "read-ok"


@pytest.mark.asyncio
async def test_acquisition_failure_is_a_tool_error() -> None:
    acquirer = MagicMock()
    acquirer.ensure_ready = AsyncMock(side_effect=RuntimeError("node failed"))

    results = await _runner(acquirer).execute_batch(
        [ToolCallData(id="call-1", name="remote_read", arguments={})],
        ToolExecutionContext(turn=1, max_turns=10),
    )

    result = results[0][1]
    assert result.status == "error"
    assert result.content == "node failed"
    assert result.meta["layer"] == "bohrium_node_acquisition"


@pytest.mark.asyncio
async def test_bohrium_query_stays_cold_but_submit_acquires_before_execution() -> None:
    acquirer = MagicMock()
    acquirer.ensure_ready = AsyncMock(return_value=MagicMock())
    runner = _runner(acquirer, _BohriumControlTool())

    query_results = await runner.execute_batch(
        [
            ToolCallData(
                id="call-query",
                name="bohrium_control",
                arguments={"action": "query"},
            )
        ],
        ToolExecutionContext(turn=1, max_turns=10),
    )
    acquirer.ensure_ready.assert_not_awaited()

    submit_results = await runner.execute_batch(
        [
            ToolCallData(
                id="call-submit",
                name="bohrium_control",
                arguments={"action": "submit"},
            )
        ],
        ToolExecutionContext(turn=2, max_turns=10),
    )

    assert query_results[0][1].content == "query"
    assert submit_results[0][1].content == "submit"
    acquirer.ensure_ready.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_unavailable_error_has_terminal_retry_metadata() -> None:
    acquirer = MagicMock()
    terminal = BohriumNodeUnavailableError.from_failure(
        ConnectionError("offline"),
        reason="session.read_file",
    )
    acquirer.ensure_ready = AsyncMock(side_effect=terminal)

    results = await _runner(acquirer).execute_batch(
        [ToolCallData(id="call-1", name="remote_read", arguments={})],
        ToolExecutionContext(turn=1, max_turns=10),
    )

    result = results[0][1]
    assert result.status == "error"
    assert result.meta["error_code"] == "BOHRIUM_NODE_UNAVAILABLE"
    assert result.meta["retryable"] is False
    assert result.meta["failure_scope"] == "run"
    assert result.meta["terminal_on_repeat"] is True


def test_open_circuit_hides_node_tools_and_restricts_bohrium_actions() -> None:
    acquirer = MagicMock()
    acquirer.unavailable_for_run = True

    session_runner = _runner(acquirer)
    session_definitions = session_runner._catalog.build_definitions()
    assert session_runner.filter_model_tool_definitions(session_definitions) == []

    bohrium_runner = _runner(acquirer, _BohriumControlTool())
    definitions = bohrium_runner._catalog.build_definitions()
    filtered = bohrium_runner.filter_model_tool_definitions(definitions)

    assert filtered is not None
    actions = filtered[0]["function"]["parameters"]["properties"]["action"]["enum"]
    assert actions == ["query"]
