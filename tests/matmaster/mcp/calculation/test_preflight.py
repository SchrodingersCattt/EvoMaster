from __future__ import annotations

from unittest.mock import MagicMock

from matmaster.bohrium.runtime import BohriumRuntimeHandle
from matmaster.mcp.calculation.preflight import CalculationPreflight


def test_prepare_call_builds_submission_and_materializes_selected_inputs():
    runtime = MagicMock(spec=BohriumRuntimeHandle)
    runtime.build_submission.return_value = MagicMock(
        executor={"type": "local"},
        storage={"type": "https"},
        submission_mode="sync",
    )
    runtime.materialize_input_path.side_effect = (
        lambda value, **_: f"https://oss/{value}"
    )

    preflight = CalculationPreflight(
        calculation_executors={"mat_sg": {"sync_tools": ["run"]}}
    )
    args = {"input_path": "a.in"}
    schema = {
        "type": "object",
        "properties": {"input_path": {"type": "string", "format": "path"}},
    }

    resolved = preflight.prepare_call(
        workspace_path="/tmp/work",
        args=args,
        tool_name="mat_sg_run",
        remote_tool_name="run",
        server_name="mat_sg",
        input_schema=schema,
        tool_description="Args:\n    input_path (Path): input file",
        runtime=runtime,
        session=None,
    )

    assert resolved["executor"] == {"type": "local"}
    assert resolved["storage"] == {"type": "https"}
    assert resolved["input_path"] == "https://oss/a.in"


def test_prepare_call_resolves_model_alias_before_path_materialization():
    runtime = MagicMock(spec=BohriumRuntimeHandle)
    runtime.build_submission.return_value = MagicMock(
        executor={"type": "dispatcher"},
        storage={"type": "https"},
        submission_mode="async",
    )
    runtime.materialize_input_path.side_effect = lambda value, **_: value

    preflight = CalculationPreflight(
        calculation_executors={
            "mat_sg": {
                "executor_map": {
                    "submit_run": {
                        "type": "dispatcher",
                        "machine": {"remote_profile": {}},
                    }
                }
            }
        }
    )
    resolved = preflight.prepare_call(
        workspace_path="/tmp/work",
        args={"model_path": "DPA2.4-7M"},
        tool_name="mat_sg_submit_run",
        remote_tool_name="submit_run",
        server_name="mat_sg",
        input_schema={
            "type": "object",
            "properties": {"model_path": {"type": "string", "format": "path"}},
        },
        tool_description=(
            "Args:\n"
            "    model_path (Path): model file. Aliases: "
            "{'DPA2.4-7M': 'https://oss/models/dpa-2.4-7M.pt'}"
        ),
        runtime=runtime,
        session=None,
    )

    assert resolved["model_path"] == "https://oss/models/dpa-2.4-7M.pt"
