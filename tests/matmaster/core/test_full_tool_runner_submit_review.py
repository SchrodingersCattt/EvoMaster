from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from matmaster.core.submit_review_support import (
    RESUBMIT_SIGNATURES_KEY,
    RUN_IDENTITY_KEY,
    SUBMIT_APPROVAL_GATE_KEY,
)
from matmaster.tools.builtin.bohrium_tool.submit_review import (
    BohriumSubmitReviewProvider,
)
from matmaster.tools.tool_result import ToolResult
from matmaster.types.messages import ToolCallData
from matmaster.types.run_metadata import RunIdentity
from matmaster.types.submit_review import SubmitReviewDecision
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolBinding, ToolInstance, ToolSpec
from matmaster.types.topology import RuntimeTopology, ToolPlane
from tests.matmaster.core.test_full_tool_runner import (
    _make_ctx,
    _make_runner,
    _make_tc,
    _make_topology,
)


class _SubmitGate:
    def __init__(self, decision: SubmitReviewDecision) -> None:
        self.decision = decision
        self.calls = 0
        self.requests = []

    async def review(self, request):
        self.calls += 1
        self.requests.append(request)
        return self.decision


class _MultiGate:
    """Return decisions by tool_call_id and record review order."""

    def __init__(self, decisions: dict[str, SubmitReviewDecision]) -> None:
        self.decisions = decisions
        self.reviewed: list[str] = []

    async def review(self, request):
        self.reviewed.append(request.tool_call_id)
        return self.decisions[request.tool_call_id]


class _SubmitCapture:
    def __init__(self) -> None:
        self.last: dict[str, Any] | None = None
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, args: dict[str, Any], exec_ctx: Any) -> ToolResult:
        self.last = dict(args)
        self.calls.append(dict(args))
        return ToolResult(
            status="success",
            content=json.dumps({"success": True, "job_id": "job-123"}),
            meta={
                "submit_execution_audit": {
                    "execution_attempted": True,
                    "external_effect_started": True,
                    "job_id": "job-123",
                }
            },
        )


def _submit_call(
    *,
    call_id: str = "call_submit",
    input_dir: str = "/share/c",
    image: str = "img",
    cmd: str = "run",
    job_name: str = "matmaster-job",
) -> ToolCallData:
    return _make_tc(
        "Bohrium",
        call_id=call_id,
        action="submit",
        input_dir=input_dir,
        image=image,
        cmd=cmd,
        job_name=job_name,
    )


def _make_submit_runner(
    gate: _SubmitGate | None = None,
    *,
    topology: RuntimeTopology | None = None,
    policy: Any | None = None,
):
    capture = _SubmitCapture()
    spec = ToolSpec(
        tool_name="Bohrium",
        description="submit review test tool",
        args_schema={},
        source="test",
        effect_level="external_effect",
        fast_path_eligible=False,
    )
    binding = ToolBinding(
        binding_key="external_service:Bohrium",
        plane=ToolPlane.EXTERNAL_SERVICE,
        resource_claims=(),
    )
    instance = ToolInstance(
        tool_spec=spec,
        tool_binding=binding,
        tool_executor=capture,
        submit_review_provider=BohriumSubmitReviewProvider(),
    )
    catalog = MagicMock()
    catalog.get_tool.return_value = instance
    state = ToolRunnerState()
    state.set(RUN_IDENTITY_KEY, RunIdentity(task_id="task-1", session_id="sess-1"))
    if gate is not None:
        state.set(SUBMIT_APPROVAL_GATE_KEY, gate)
    runner = _make_runner(
        catalog,
        topology=topology or _make_topology(),
        policy=policy,
        state=state,
    )
    return runner, capture, state


class TestSubmitReviewGate:
    @pytest.mark.asyncio
    async def test_gate_absent_passes_through(self) -> None:
        runner, capture, _state = _make_submit_runner(gate=None)

        results = await runner.execute_batch([_submit_call()], _make_ctx())

        assert results[0][1].status == "success"
        assert capture.last is not None
        assert capture.last["cmd"] == "run"

    @pytest.mark.asyncio
    async def test_rejected_blocks_without_external_effect_and_arms_guard(
        self,
    ) -> None:
        gate = _SubmitGate(
            SubmitReviewDecision(
                user_decision="reject",
                review_outcome="rejected",
                final_arguments={
                    "action": "submit",
                    "input_dir": "/share/c",
                    "image": "img2",
                    "cmd": "run > log 2>&1",
                    "job_name": "matmaster-job",
                },
            )
        )
        runner, capture, state = _make_submit_runner(gate)

        first = await runner.execute_batch([_submit_call()], _make_ctx())

        first_result = first[0][1]
        assert first_result.status == "blocked"
        assert "review" in json.loads(first_result.content)
        assert capture.last is None
        assert gate.calls == 1
        assert len(state.get(RESUBMIT_SIGNATURES_KEY)) == 2

        second = await runner.execute_batch([_submit_call()], _make_ctx())

        assert second[0][1].status == "blocked"
        assert gate.calls == 1

        await runner.execute_batch(
            [_submit_call(call_id="call_other", cmd="run-other")],
            _make_ctx(),
        )
        assert gate.calls == 2

    @pytest.mark.asyncio
    async def test_timeout_and_busy_block_and_arm_guard(self) -> None:
        for outcome in ("timeout", "busy"):
            gate = _SubmitGate(
                SubmitReviewDecision(user_decision=None, review_outcome=outcome)
            )
            runner, capture, state = _make_submit_runner(gate)

            results = await runner.execute_batch([_submit_call()], _make_ctx())

            assert results[0][1].status == "blocked"
            assert capture.last is None
            assert state.get(RESUBMIT_SIGNATURES_KEY)

    @pytest.mark.asyncio
    async def test_cancelled_yields_cancelled_result_not_raise(self) -> None:
        gate = _SubmitGate(
            SubmitReviewDecision(user_decision=None, review_outcome="cancelled")
        )
        runner, capture, state = _make_submit_runner(gate)

        results = await runner.execute_batch([_submit_call()], _make_ctx())

        assert results[0][1].status == "cancelled"
        assert capture.last is None
        assert not state.get(RESUBMIT_SIGNATURES_KEY)

    @pytest.mark.asyncio
    async def test_oversized_submit_arg_errors_not_submit(self) -> None:
        gate = _SubmitGate(
            SubmitReviewDecision(user_decision="submit", review_outcome="approved")
        )
        runner, capture, _state = _make_submit_runner(gate)

        results = await runner.execute_batch(
            [_submit_call(cmd="x" * 9000)],
            _make_ctx(),
        )

        assert results[0][1].status == "error"
        assert "too long" in results[0][1].content
        assert gate.calls == 0
        assert capture.last is None

    @pytest.mark.asyncio
    async def test_approved_runs_with_user_edited_execution_args(self) -> None:
        gate = _SubmitGate(
            SubmitReviewDecision(
                user_decision="submit",
                review_outcome="approved",
                final_arguments={
                    "action": "submit",
                    "input_dir": "/share/c",
                    "image": "new",
                    "cmd": "run --x > log 2>&1",
                    "machine": "c64_m256_cpu",
                    "job_name": "matmaster-job",
                },
                reported_input_file_changes=[
                    {"relative_path": "input.in", "lines": "1"}
                ],
            )
        )
        runner, capture, state = _make_submit_runner(gate)

        await runner.execute_batch([_submit_call()], _make_ctx())

        assert gate.calls == 1
        assert gate.requests[0].request_id.startswith("sr_")
        assert gate.requests[0].task_id == "task-1"
        assert gate.requests[0].session_id == "sess-1"
        assert capture.last["machine"] == "c64_m256_cpu"
        assert capture.last["cmd"] == "run --x > log 2>&1"
        assert capture.last["disk_size"] == 50
        records = state.get("submit_review_records")
        assert (
            records["call_submit"]["review_content"]["parameter_changes"]["cmd"]["to"]
            == "run --x > log 2>&1"
        )

    @pytest.mark.asyncio
    async def test_approved_invalid_final_arguments_returns_error(self) -> None:
        gate = _SubmitGate(
            SubmitReviewDecision(
                user_decision="submit",
                review_outcome="approved",
                final_arguments={
                    "action": "submit",
                    "input_dir": "/share/c",
                    "image": "new",
                    "cmd": "run --x > log 2>&1",
                    "disk_size": "huge",
                },
            )
        )
        runner, capture, _state = _make_submit_runner(gate)

        results = await runner.execute_batch([_submit_call()], _make_ctx())

        assert results[0][1].status == "error"
        assert "disk_size" in results[0][1].content
        assert capture.last is None

    @pytest.mark.asyncio
    async def test_approved_serial_deny_keeps_submit_review_record(self) -> None:
        gate = _SubmitGate(
            SubmitReviewDecision(
                user_decision="submit",
                review_outcome="approved",
                final_arguments={
                    "action": "submit",
                    "input_dir": "/share/c",
                    "image": "new",
                    "cmd": "run --x > log 2>&1",
                },
            )
        )
        topology = RuntimeTopology(
            session_kind="local",
            control_root="/tmp/ctrl",
            workspace_root="/tmp/ws",
            active_planes=frozenset(),
        )
        runner, capture, _state = _make_submit_runner(gate, topology=topology)

        results = await runner.execute_batch([_submit_call()], _make_ctx())

        result = results[0][1]
        assert result.status == "error"
        assert result.meta["layer"] == "structural"
        assert "review" in json.loads(result.content)
        assert result.payload["bohrium_submit_review"]["review_outcome"] == "approved"
        assert capture.last is None

    @pytest.mark.asyncio
    async def test_edit_truncates_following_submits(self) -> None:
        gate = _MultiGate(
            {
                "s1": SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments=None,
                ),
                "s2": SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments={
                        "action": "submit",
                        "input_dir": "/share/c",
                        "image": "img",
                        "cmd": "edited --x > log 2>&1",
                        "machine": "c32_m128_cpu",
                        "job_name": "matmaster-job",
                        "disk_size": 50,
                    },
                ),
            }
        )
        runner, capture, _state = _make_submit_runner(gate)

        results = await runner.execute_batch(
            [
                _submit_call(call_id="s1", cmd="alpha"),
                _submit_call(call_id="s2", cmd="beta"),
                _submit_call(call_id="s3", cmd="gamma"),
                _submit_call(call_id="s4", cmd="delta"),
            ],
            _make_ctx(),
        )

        assert gate.reviewed == ["s1", "s2"]
        assert len(capture.calls) == 2
        assert {c["cmd"] for c in capture.calls} == {
            "alpha > log 2>&1",
            "edited --x > log 2>&1",
        }
        for idx in (2, 3):
            tr = results[idx][1]
            assert tr.status == "blocked"
            body = json.loads(tr.content)
            assert body["status"] == "SupersededByPriorEdit"
            assert tr.meta["block_reason"] == "SupersededByPriorEdit"
            assert tr.meta["layer"] == "submit_approval_gate"
            assert tr.meta["superseded_by"] == "s2"
            assert tr.meta["changed_fields"] == ["cmd"]

    @pytest.mark.asyncio
    async def test_no_edit_all_submits_confirmed(self) -> None:
        gate = _MultiGate(
            {
                cid: SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments=None,
                )
                for cid in ("s1", "s2", "s3", "s4")
            }
        )
        runner, capture, _state = _make_submit_runner(gate)

        results = await runner.execute_batch(
            [
                _submit_call(call_id="s1", cmd="a"),
                _submit_call(call_id="s2", cmd="b"),
                _submit_call(call_id="s3", cmd="c"),
                _submit_call(call_id="s4", cmd="d"),
            ],
            _make_ctx(),
        )

        assert gate.reviewed == ["s1", "s2", "s3", "s4"]
        assert len(capture.calls) == 4
        for idx in range(4):
            assert results[idx][1].status == "success"

    @pytest.mark.asyncio
    async def test_truncated_submit_not_armed_into_guard(self) -> None:
        gate = _MultiGate(
            {
                "s1": SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments=None,
                ),
                "s2": SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments={
                        "action": "submit",
                        "input_dir": "/share/c",
                        "image": "img",
                        "cmd": "edited > log 2>&1",
                        "machine": "c32_m128_cpu",
                        "job_name": "matmaster-job",
                        "disk_size": 50,
                    },
                ),
            }
        )
        runner, _capture, state = _make_submit_runner(gate)

        await runner.execute_batch(
            [
                _submit_call(call_id="s1", cmd="alpha"),
                _submit_call(call_id="s2", cmd="beta"),
                _submit_call(call_id="s3", cmd="gamma"),
            ],
            _make_ctx(),
        )

        assert not state.get(RESUBMIT_SIGNATURES_KEY)

        gate2 = _MultiGate(
            {
                "s3": SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments=None,
                )
            }
        )
        state.set(SUBMIT_APPROVAL_GATE_KEY, gate2)

        results2 = await runner.execute_batch(
            [_submit_call(call_id="s3", cmd="gamma")],
            _make_ctx(),
        )

        assert gate2.reviewed == ["s3"]
        assert results2[0][1].status == "success"

    @pytest.mark.asyncio
    async def test_truncation_only_blocks_submits_not_other_tools(self) -> None:
        gate = _MultiGate(
            {
                "s1": SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments={
                        "action": "submit",
                        "input_dir": "/share/c",
                        "image": "img",
                        "cmd": "edited > log 2>&1",
                        "machine": "c32_m128_cpu",
                        "job_name": "matmaster-job",
                        "disk_size": 50,
                    },
                ),
            }
        )
        runner, capture, _state = _make_submit_runner(gate)

        results = await runner.execute_batch(
            [
                _submit_call(call_id="s1", cmd="beta"),
                _make_tc("Bohrium", call_id="q1", action="query", note="ping"),
                _submit_call(call_id="s2", cmd="gamma"),
            ],
            _make_ctx(),
        )

        assert gate.reviewed == ["s1"]
        assert any(c.get("action") == "query" for c in capture.calls)
        assert results[2][1].status == "blocked"
        assert json.loads(results[2][1].content)["status"] == ("SupersededByPriorEdit")

    @pytest.mark.asyncio
    async def test_normalization_only_does_not_truncate(self) -> None:
        provider = BohriumSubmitReviewProvider()
        s1_canonical = provider.build_review_draft(
            {
                "action": "submit",
                "input_dir": "/share/c",
                "image": "img",
                "cmd": "alpha",
                "job_name": "matmaster-job",
            }
        ).review_draft_arguments
        s1_final = dict(s1_canonical)
        s1_final["disk_size"] = str(s1_final["disk_size"])
        gate = _MultiGate(
            {
                "s1": SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments=s1_final,
                ),
                "s2": SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments=None,
                ),
            }
        )
        runner, capture, _state = _make_submit_runner(gate)

        results = await runner.execute_batch(
            [
                _submit_call(call_id="s1", cmd="alpha"),
                _submit_call(call_id="s2", cmd="beta"),
            ],
            _make_ctx(),
        )

        assert gate.reviewed == ["s1", "s2"]
        assert len(capture.calls) == 2
        assert results[1][1].status == "success"

    @pytest.mark.asyncio
    async def test_reported_file_change_truncates_following(self) -> None:
        gate = _MultiGate(
            {
                "s1": SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments=None,
                    reported_input_file_changes=[
                        {"relative_path": "input.in", "lines": "1-5"}
                    ],
                ),
            }
        )
        runner, capture, _state = _make_submit_runner(gate)

        results = await runner.execute_batch(
            [
                _submit_call(call_id="s1", cmd="alpha"),
                _submit_call(call_id="s2", cmd="beta"),
            ],
            _make_ctx(),
        )

        assert gate.reviewed == ["s1"]
        assert len(capture.calls) == 1
        assert capture.calls[0]["cmd"] == "alpha > log 2>&1"
        tr = results[1][1]
        assert tr.status == "blocked"
        assert json.loads(tr.content)["status"] == "SupersededByPriorEdit"
        assert tr.meta["superseded_by"] == "s1"
        assert tr.meta["changed_fields"] == ["input_files"]

    @pytest.mark.asyncio
    async def test_optout_batch_unaffected_when_gate_absent(self) -> None:
        runner, capture, _state = _make_submit_runner(gate=None)

        results = await runner.execute_batch(
            [
                _submit_call(call_id="s1", cmd="alpha"),
                _submit_call(call_id="s2", cmd="beta"),
                _submit_call(call_id="s3", cmd="gamma"),
            ],
            _make_ctx(),
        )

        assert len(capture.calls) == 3
        for idx in range(3):
            assert results[idx][1].status == "success"
