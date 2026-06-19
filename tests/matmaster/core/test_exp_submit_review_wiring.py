from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from matmaster.config.exp import ExpConfig
from matmaster.core.exp import Exp
from matmaster.core.hooks import HookEvent, PostToolCallContext
from matmaster.core.playground import ExecutionEnvironment
from matmaster.core.run_context import AgentRunContext, AgentRunRequest
from matmaster.core.submit_review_support import (
    RUN_IDENTITY_KEY,
    SUBMIT_APPROVAL_GATE_KEY,
    SUBMIT_REVIEW_RECORDS_KEY,
)
from matmaster.tools.tool_result import ToolResult
from matmaster.types.run_metadata import RunMetadata
from matmaster.types.runtime_ports import AgentRunPorts
from tests.matmaster.core.conftest import MockLLMProvider


class _SubmitGate:
    async def review(self, request):
        raise AssertionError("not used in build_runtime hook wiring test")


async def test_submit_review_gate_wires_runner_state_and_post_hooks(
    tmp_path: Path,
) -> None:
    gate = _SubmitGate()
    exp = Exp(ExpConfig(name="test"))
    ctx = AgentRunContext(
        environment=ExecutionEnvironment(
            workdir=tmp_path,
            execution_workdir=str(tmp_path / "exec"),
            session_type="local",
            cache_area=tmp_path / "cache",
            session_id="session-1",
            metadata=RunMetadata(task_id="task-1"),
        ),
        request=AgentRunRequest(
            llm_provider=MockLLMProvider(),
            ports=AgentRunPorts(submit_approval_gate=gate),
        ),
    )

    with patch("matmaster.core.agent.AgentKernel"):
        runtime = await exp.build_runtime(ctx)

    runner = runtime.kernel_runtime.resources.tool_runner
    state = runner.state
    assert state.get(SUBMIT_APPROVAL_GATE_KEY) is gate
    assert state.get(RUN_IDENTITY_KEY).task_id == "task-1"
    assert state.get(RUN_IDENTITY_KEY).session_id == "session-1"

    review = {"parameter_changes": {"cmd": {"from": "a", "to": "b"}}}
    audit = {
        "schema_version": 1,
        "request_id": "sr_1",
        "review_outcome": "approved",
    }
    state.set(
        SUBMIT_REVIEW_RECORDS_KEY,
        {"call_1": {"review_content": review, "audit_baseline": audit}},
    )
    hook_executor = runtime.kernel_runtime.resources.hook_executor

    async def destructive_rewrite(ctx, result):
        body = json.loads(result.content)
        body.pop("review", None)
        return result.model_copy(update={"content": json.dumps(body), "payload": {}})

    hook_executor._rewriters[HookEvent.POST_TOOL_CALL].insert(
        1,
        destructive_rewrite,
    )
    result = ToolResult(
        status="error",
        content=json.dumps({"success": False, "status": "UploadFailed"}),
        meta={
            "submit_execution_audit": {
                "execution_attempted": True,
                "external_effect_started": True,
                "job_create_attempted": True,
                "job_id": "create-job-id",
                "input_upload_attempted": True,
                "job_add_attempted": False,
            }
        },
    )
    post_ctx = PostToolCallContext(
        tool_name="Bohrium",
        tool_call_id="call_1",
        arguments={},
        result=result,
        turn=1,
    )

    out = await hook_executor.emit_rewrite(
        HookEvent.POST_TOOL_CALL,
        post_ctx,
        result,
    )

    body = json.loads(out.content)
    assert body["review"]["parameter_changes"]["cmd"]["to"] == "b"
    audit_out = out.payload["bohrium_submit_review"]
    assert audit_out["external_effect_started"] is True
    assert audit_out["job_id"] == "create-job-id"
    assert audit_out["job_add_attempted"] is False
    assert "submit_execution_audit" not in out.meta
