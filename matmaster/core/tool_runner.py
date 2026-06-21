"""ToolRunner Protocol and FullToolRunner implementation.

ToolRunner defines the execution strategy interface for tool calls.

FullToolRunner executes the Tool Runtime v2 pipeline:
Catalog -> StructuralValidation -> CapabilityPolicy -> fast path ->
Scheduler -> executor -> release.

ToolExecutionContext carries per-batch execution metadata (turn, max_turns,
cancel_token).
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import uuid4

from matmaster.core.hooks import (
    HookEvent,
    HookExecutor,
    HookOutcome,
    PostToolCallContext,
    PreToolCallContext,
)
from matmaster.core.structural_validation import StructuralValidation
from matmaster.core.submit_review_support import (
    RESUBMIT_SIGNATURES_KEY,
    RUN_IDENTITY_KEY,
    SUBMIT_APPROVAL_GATE_KEY,
    SUBMIT_REVIEW_RECORDS_KEY,
    SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY,
    attach_submit_review_record,
    build_audit_payload,
    build_review_content,
    compute_parameter_changes,
    submit_signature,
)
from matmaster.core.tool_scheduler import SchedulerTicket, ToolScheduler
from matmaster.tools.tool_catalog import ToolCatalog
from matmaster.tools.tool_result import ToolResult, normalize_tool_result
from matmaster.types.cancellation import CancellationToken
from matmaster.types.messages import ToolCallData
from matmaster.types.submit_review import (
    SubmitReviewArgumentError,
    SubmitReviewDecision,
    SubmitReviewRequest,
)
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolExecutionContext as _ExecCtx
from matmaster.types.tool_spec import ToolInstance
from matmaster.types.topology import RuntimeTopology

if TYPE_CHECKING:
    from matmaster.core.capability_policy import CapabilityPolicy

_OUTCOME_STATUS = {
    "rejected": "UserRejected",
    "timeout": "ReviewTimeout",
    "busy": "ReviewBusy",
}
_OUTCOME_MESSAGE = {
    "rejected": (
        "用户拒绝了本次 Bohrium 提交。请不要重新提交本作业，可总结当前进展、"
        "转去做其它工作，或结束本轮等待用户继续反馈。"
    ),
    "timeout": (
        "本次提交未在限定时间内获得用户确认，未提交。请不要重新提交本作业，"
        "可总结进展或转做其它工作。"
    ),
    "busy": (
        "当前已有待处理的人机交互，本次提交未发起确认，未提交。"
        "请稍后由用户处理后再继续，不要重复提交。"
    ),
}


def _gate_block_result(
    status: str, message: str, *, result_status: str = "blocked"
) -> ToolResult:
    return ToolResult(
        status=result_status,
        content=json.dumps(
            {"success": False, "status": status, "message": message},
            ensure_ascii=False,
        ),
        meta={"block_reason": status, "layer": "submit_approval_gate"},
    )


@dataclass
class BatchExecutionContext:
    """Per-batch execution context used internally by FullToolRunner.

    Carries the current turn number and an optional cancel token for cancellation.
    """

    turn: int
    max_turns: int
    cancel_token: CancellationToken | None = None
    progress_sink: Callable[[str, str, str], Awaitable[None]] | None = None


# Backward-compatible alias
ToolExecutionContext = BatchExecutionContext


@runtime_checkable
class ToolRunner(Protocol):
    """Protocol for tool execution strategies.

    execute_batch processes a list of tool calls and returns
    (ToolCallData, ToolResult) pairs in the same order as input.
    """

    async def execute_batch(
        self,
        tool_calls: list[ToolCallData],
        ctx: ToolExecutionContext,
        *,
        on_result: Callable[[ToolCallData, ToolResult], Awaitable[None]] | None = None,
    ) -> list[tuple[ToolCallData, ToolResult]]: ...


class FullToolRunner:
    """Complete ToolRunner: Catalog -> Validation -> Policy -> Scheduler -> Execute -> Release.

    Per D-05: Strictly follows spec section 9.1 execution chain.
    Per D-06: Each layer produces ToolResult with meta["layer"] marking failure source.
    """

    def __init__(
        self,
        catalog: ToolCatalog,
        structural_validation: StructuralValidation,
        capability_policy: CapabilityPolicy,
        scheduler: ToolScheduler,
        topology: RuntimeTopology,
        hook_executor: HookExecutor | None = None,
        state: ToolRunnerState | None = None,
    ) -> None:
        self._catalog = catalog
        self._validation = structural_validation
        self._policy = capability_policy
        self._scheduler = scheduler
        self._topology = topology
        self._hook_executor = hook_executor
        self._state = state or ToolRunnerState()

    @property
    def state(self) -> ToolRunnerState:
        return self._state

    def _truncate_result(
        self, tr: ToolResult, max_chars: int, tool_call_id: str
    ) -> ToolResult:
        """Truncate oversized content, save full result to disk."""
        from pathlib import Path

        # Save full content to control_root (always local)
        results_dir = Path(self._topology.control_root) / ".tool_results"
        results_dir.mkdir(parents=True, exist_ok=True)
        full_path = results_dir / f"{tool_call_id}.txt"
        full_path.write_text(tr.content, encoding="utf-8")

        # Truncate
        tail_len = min(2000, max_chars // 4)
        head = tr.content[: max_chars // 2]
        tail = tr.content[-tail_len:] if tail_len > 0 else ""
        truncated_chars = len(tr.content) - len(head) - len(tail)
        notice = (
            f"\n\n... [{truncated_chars} chars truncated; "
            f"re-run with more specific parameters to see full output] ...\n\n"
        )
        truncated_content = head + notice + tail

        new_meta = {**tr.meta, "full_result_path": str(full_path), "truncated": True}
        return ToolResult(
            status=tr.status,
            content=truncated_content,
            payload=tr.payload,
            meta=new_meta,
            images=tr.images,
        )

    async def execute_batch(
        self,
        tool_calls: list[ToolCallData],
        ctx: ToolExecutionContext,
        *,
        on_result: Callable[[ToolCallData, ToolResult], Awaitable[None]] | None = None,
    ) -> list[tuple[ToolCallData, ToolResult]]:
        """Two-step tool execution.

        Step 1 (serial): validate each call through the constraint layers.
        Step 2 (concurrent): execute all approved calls via asyncio.gather.

        Argument immutability contract:
        every consumer along this chain must treat the received arguments dict
        as read-only. This applies to pre/post hooks, StructuralValidation,
        input_validator callables, CapabilityPolicy, and tool_executor. In-place
        edits such as arguments[k] = v, update(), pop(), clear(), or setdefault()
        are forbidden because ToolCallData caches arguments_json.

        decision.modified_args is the sanctioned way for validation layers to
        derive a changed argument set: it builds a fresh dict and keeps the
        original tc.arguments untouched. Tool implementations that need adjusted
        parameters must construct their own fresh dict.

        The runner defensively deep-copies tc.arguments before handing data to
        each layer so a buggy consumer cannot stale the original ToolCallData
        cache, but this is a cache-safety backstop rather than permission to
        mutate layer inputs.

        Returns list of (ToolCallData, ToolResult) in input order.
        """
        n = len(tool_calls)
        results: list[tuple[ToolCallData, ToolResult] | None] = [None] * n
        approved: list[tuple[int, ToolCallData, ToolInstance, dict[str, Any], bool]] = (
            []
        )

        def _record_for(tool_call_id: str) -> dict[str, Any] | None:
            records = self._state.get(SUBMIT_REVIEW_RECORDS_KEY) or {}
            return records.get(tool_call_id)

        def _attach_serial_review_record(
            tc: ToolCallData,
            tr: ToolResult,
            *,
            block_reason: str | None = None,
        ) -> ToolResult:
            record = _record_for(tc.id)
            if record is None:
                return tr
            return attach_submit_review_record(
                tr,
                record["review_content"],
                record["audit_baseline"],
                block_reason=block_reason,
            )

        def _review_record(
            *,
            request_id: str,
            session_id: str,
            task_id: str,
            tool_call_id: str,
            review_outcome: str,
            user_decision: str | None,
            model_arguments: dict[str, Any],
            review_draft_arguments: dict[str, Any],
            final_arguments: dict[str, Any],
            execution_args: dict[str, Any] | None,
            normalization_changes: dict[str, Any],
            user_changes: dict[str, Any],
            execution_normalization_changes: dict[str, Any],
            reported: list[dict[str, Any]],
        ) -> dict[str, Any]:
            return {
                "review_content": build_review_content(user_changes, reported),
                "audit_baseline": build_audit_payload(
                    request_id=request_id,
                    session_id=session_id,
                    task_id=task_id,
                    tool_call_id=tool_call_id,
                    review_outcome=review_outcome,
                    user_decision=user_decision,
                    model_arguments=model_arguments,
                    review_draft_arguments=review_draft_arguments,
                    final_arguments=final_arguments,
                    execution_arguments=execution_args,
                    normalization_changes=normalization_changes,
                    user_parameter_changes=user_changes,
                    execution_normalization_changes=execution_normalization_changes,
                    reported_input_file_changes=reported,
                    reported_input_file_change_count=len(reported),
                    execution_audit=None,
                ),
            }

        # Batch-local truncation flag: set when a user edit supersedes the
        # rest of this batch's submits. Local to this execute_batch call, so it
        # never accumulates across turns.
        superseding_edit: tuple[str, list[str]] | None = None

        # ── Serial validation ──────────────────────────────
        for idx, tc in enumerate(tool_calls):
            # 1. Catalog lookup
            instance = self._catalog.get_tool(tc.name)
            if instance is None:
                tr = ToolResult(
                    status="error",
                    content=f"Unknown tool: {tc.name}",
                    meta={"layer": "catalog"},
                )
                results[idx] = (tc, tr)
                if on_result:
                    await on_result(tc, tr)
                continue

            # 2. Reject unparseable tool-call arguments (_raw fallback)
            if "_raw" in tc.arguments:
                tr = ToolResult(
                    status="error",
                    content=(
                        "Tool call arguments could not be parsed as valid JSON. "
                        "Please simplify argument content and retry."
                    ),
                    meta={"layer": "tool_runner", "reason": "raw_fallback"},
                )
                results[idx] = (tc, tr)
                if on_result:
                    await on_result(tc, tr)
                continue

            base_args = copy.deepcopy(tc.arguments)

            if self._hook_executor is not None:
                pre_ctx = PreToolCallContext(
                    tool_name=tc.name,
                    tool_call_id=tc.id,
                    arguments=copy.deepcopy(base_args),
                    turn=ctx.turn,
                )
                await self._hook_executor.emit(HookEvent.PRE_TOOL_CALL, pre_ctx)
                hook_result = await self._hook_executor.emit_intercept(
                    HookEvent.PRE_TOOL_CALL, pre_ctx
                )
                if hook_result.outcome == HookOutcome.BLOCK:
                    tr = ToolResult(
                        status="blocked",
                        content=hook_result.message or "Blocked by hook",
                        meta={"layer": "hook"},
                    )
                    results[idx] = (tc, tr)
                    if on_result:
                        await on_result(tc, tr)
                    continue

            # 1b. Cancel check (stop_mode-aware)
            if ctx.cancel_token is not None and ctx.cancel_token.is_cancelled:
                stop_mode = instance.tool_binding.stop_mode
                if stop_mode == "cancellable":
                    tr = ToolResult(status="cancelled", content="Run cancelled.")
                    results[idx] = (tc, tr)
                    continue
                if stop_mode == "best_effort":
                    tr = ToolResult(
                        status="cancelled",
                        content=(
                            "Cancellation requested (best-effort). "
                            "Tool may have partially completed."
                        ),
                    )
                    results[idx] = (tc, tr)
                    continue

            gate = self._state.get(SUBMIT_APPROVAL_GATE_KEY)
            if gate is not None and instance.submit_review_provider is not None:
                try:
                    draft = instance.submit_review_provider.build_review_draft(
                        base_args
                    )
                except SubmitReviewArgumentError as exc:
                    tr = ToolResult(
                        status="error",
                        content=f"Submit arguments rejected: {exc}",
                    )
                    results[idx] = (tc, tr)
                    if on_result:
                        await on_result(tc, tr)
                    continue

                if draft is not None:
                    if superseding_edit is not None:
                        editor_id, changed_fields = superseding_edit
                        tr = _gate_block_result(
                            "SupersededByPriorEdit",
                            "The user modified the parameters or input files "
                            "of another submit in the same batch. This submit "
                            "was not executed; please refer to those changes "
                            "and re-evaluate before resubmitting.",
                        )
                        tr.meta["superseded_by"] = editor_id
                        tr.meta["changed_fields"] = changed_fields
                        results[idx] = (tc, tr)
                        if on_result:
                            await on_result(tc, tr)
                        continue

                    run_identity = self._state.get(RUN_IDENTITY_KEY)
                    session_id = getattr(run_identity, "session_id", "")
                    task_id = getattr(run_identity, "task_id", "")
                    guard = self._state.get(RESUBMIT_SIGNATURES_KEY)
                    if guard is None:
                        guard = set()
                        self._state.set(RESUBMIT_SIGNATURES_KEY, guard)

                    model_sig = submit_signature(draft.model_arguments)
                    if model_sig in guard:
                        tr = _gate_block_result(
                            "ResubmitBlocked",
                            "本作业已被拒绝/未获确认，请勿重复提交；"
                            "可总结进展或转做其它工作。",
                        )
                        results[idx] = (tc, tr)
                        if on_result:
                            await on_result(tc, tr)
                        continue

                    request_id = "sr_" + uuid4().hex[:12]
                    if self._state.get(SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY):
                        decision = SubmitReviewDecision(
                            user_decision="submit",
                            review_outcome="approved",
                            final_arguments=draft.review_draft_arguments,
                        )
                    else:
                        decision = await gate.review(
                            SubmitReviewRequest(
                                request_id=request_id,
                                tool_name=tc.name,
                                tool_call_id=tc.id,
                                task_id=task_id,
                                session_id=session_id,
                                draft=draft,
                            )
                        )
                    outcome = decision.review_outcome

                    if outcome == "cancelled":
                        tr = ToolResult(status="cancelled", content="Run cancelled.")
                        results[idx] = (tc, tr)
                        if on_result:
                            await on_result(tc, tr)
                        continue

                    final_args = (
                        decision.final_arguments or draft.review_draft_arguments
                    )
                    user_changes = compute_parameter_changes(
                        draft.review_draft_arguments,
                        final_args,
                    )
                    reported = decision.reported_input_file_changes or []

                    if outcome in _OUTCOME_STATUS:
                        guard.add(model_sig)
                        guard.add(submit_signature(final_args))
                        record = _review_record(
                            request_id=request_id,
                            session_id=session_id,
                            task_id=task_id,
                            tool_call_id=tc.id,
                            review_outcome=outcome,
                            user_decision=decision.user_decision,
                            model_arguments=draft.model_arguments,
                            review_draft_arguments=draft.review_draft_arguments,
                            final_arguments=final_args,
                            execution_args=None,
                            normalization_changes=draft.normalization_changes,
                            user_changes=user_changes,
                            execution_normalization_changes={},
                            reported=reported,
                        )
                        tr0 = _gate_block_result(
                            _OUTCOME_STATUS[outcome], _OUTCOME_MESSAGE[outcome]
                        )
                        tr = attach_submit_review_record(
                            tr0,
                            record["review_content"],
                            record["audit_baseline"],
                            block_reason=_OUTCOME_STATUS[outcome],
                        )
                        results[idx] = (tc, tr)
                        if on_result:
                            await on_result(tc, tr)
                        continue

                    try:
                        execution = (
                            instance.submit_review_provider.normalize_execution_args(
                                final_args
                            )
                        )
                    except SubmitReviewArgumentError as exc:
                        record = _review_record(
                            request_id=request_id,
                            session_id=session_id,
                            task_id=task_id,
                            tool_call_id=tc.id,
                            review_outcome="approved",
                            user_decision=decision.user_decision,
                            model_arguments=draft.model_arguments,
                            review_draft_arguments=draft.review_draft_arguments,
                            final_arguments=final_args,
                            execution_args=None,
                            normalization_changes=draft.normalization_changes,
                            user_changes=user_changes,
                            execution_normalization_changes={},
                            reported=reported,
                        )
                        tr0 = _gate_block_result(
                            "InvalidFinalArguments", str(exc), result_status="error"
                        )
                        tr = attach_submit_review_record(
                            tr0,
                            record["review_content"],
                            record["audit_baseline"],
                            block_reason="InvalidFinalArguments",
                        )
                        results[idx] = (tc, tr)
                        if on_result:
                            await on_result(tc, tr)
                        continue

                    record = _review_record(
                        request_id=request_id,
                        session_id=session_id,
                        task_id=task_id,
                        tool_call_id=tc.id,
                        review_outcome="approved",
                        user_decision=decision.user_decision,
                        model_arguments=draft.model_arguments,
                        review_draft_arguments=draft.review_draft_arguments,
                        final_arguments=final_args,
                        execution_args=execution.arguments,
                        normalization_changes=draft.normalization_changes,
                        user_changes=user_changes,
                        execution_normalization_changes=execution.normalization_changes,
                        reported=reported,
                    )
                    records = self._state.get(SUBMIT_REVIEW_RECORDS_KEY)
                    if records is None:
                        records = {}
                        self._state.set(SUBMIT_REVIEW_RECORDS_KEY, records)
                    records[tc.id] = record
                    base_args = execution.arguments
                    if decision.disable_future_confirmation:
                        self._state.set(SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY, True)
                    canonical_changes = compute_parameter_changes(
                        draft.review_draft_arguments, execution.arguments
                    )
                    if canonical_changes or reported:
                        changed_fields = list(canonical_changes.keys())
                        if reported:
                            changed_fields.append("input_files")
                        superseding_edit = (tc.id, changed_fields)

            # 2. StructuralValidation (Layer A)
            decision = self._validation.validate(
                self._topology,
                instance,
                copy.deepcopy(base_args),
            )
            if decision.decision == "deny":
                tr = ToolResult(
                    status="error",
                    content=decision.reason,
                    meta={"layer": "structural"},
                )
                tr = _attach_serial_review_record(tc, tr)
                results[idx] = (tc, tr)
                if on_result:
                    await on_result(tc, tr)
                continue

            effective_args = (
                copy.deepcopy(decision.modified_args)
                if decision.modified_args is not None
                else copy.deepcopy(base_args)
            )

            # 2b. input_validator
            if instance.input_validator is not None:
                try:
                    iv_decision = await instance.input_validator(
                        copy.deepcopy(effective_args),
                        self._state,
                    )
                except Exception as exc:
                    tr = ToolResult(
                        status="error",
                        content=str(exc),
                        meta={"layer": "input_validation"},
                    )
                    tr = _attach_serial_review_record(tc, tr)
                    results[idx] = (tc, tr)
                    if on_result:
                        await on_result(tc, tr)
                    continue
                if iv_decision is not None and iv_decision.decision == "deny":
                    tr = ToolResult(
                        status="error",
                        content=iv_decision.reason,
                        meta={"layer": "input_validation"},
                    )
                    tr = _attach_serial_review_record(tc, tr)
                    results[idx] = (tc, tr)
                    if on_result:
                        await on_result(tc, tr)
                    continue

            # 3. CapabilityPolicy (Layer B)
            decision = self._policy.evaluate(
                self._topology,
                instance,
                copy.deepcopy(effective_args),
            )
            if decision.decision == "deny":
                tr = ToolResult(
                    status="error",
                    content=decision.reason,
                    meta={"layer": "policy", "guidance": decision.guidance},
                )
                tr = _attach_serial_review_record(tc, tr)
                results[idx] = (tc, tr)
                if on_result:
                    await on_result(tc, tr)
                continue

            # 4. Fast path check
            claims = instance.tool_binding.resource_claims
            is_fast = (
                instance.tool_spec.effect_level == "none"
                and all(c.mode == "shared_read" for c in claims)
                and instance.tool_spec.fast_path_eligible
            )

            approved.append((idx, tc, instance, copy.deepcopy(effective_args), is_fast))

        # ── Concurrent execution ───────────────────────────
        if approved:
            exec_ctx = _ExecCtx(
                cancel_token=ctx.cancel_token,
                runner_state=self._state,
            )
            await asyncio.gather(
                *(
                    self._execute_one(
                        idx,
                        tc,
                        instance,
                        effective_args,
                        is_fast,
                        exec_ctx,
                        ctx,
                        results,
                        on_result,
                    )
                    for idx, tc, instance, effective_args, is_fast in approved
                )
            )

        return [pair for pair in results if pair is not None]

    async def _execute_one(
        self,
        idx: int,
        tc: ToolCallData,
        instance: ToolInstance,
        effective_args: dict[str, Any],
        is_fast: bool,
        exec_ctx: _ExecCtx,
        batch_ctx: BatchExecutionContext,
        results: list[tuple[ToolCallData, ToolResult] | None],
        on_result: Callable[[ToolCallData, ToolResult], Awaitable[None]] | None,
    ) -> None:
        """Execute a single approved tool call (scheduler + executor + normalize)."""
        # Scheduler acquire (skip for fast path)
        ticket: SchedulerTicket | None = None
        if not is_fast:
            claims = instance.tool_binding.resource_claims
            ticket = await self._scheduler.acquire(
                claims, timeout=self._scheduler._default_timeout
            )
            if ticket is None:
                tr = ToolResult(
                    status="error",
                    content="Scheduling timeout",
                    meta={"layer": "scheduler"},
                )
                results[idx] = (tc, tr)
                if on_result:
                    await on_result(tc, tr)
                return

        # Execute + Release
        try:
            call_exec_ctx = replace(exec_ctx, tool_call_id=tc.id)
            tr = await instance.tool_executor(
                copy.deepcopy(effective_args),
                call_exec_ctx,
            )
        except asyncio.CancelledError:
            tr = ToolResult(status="cancelled", content="Run cancelled.")
        except Exception as e:
            tr = ToolResult.from_error(tc.name, e)
        finally:
            if ticket is not None:
                await self._scheduler.release(ticket)

        # Normalize
        tr = normalize_tool_result(tr)

        # Error-wrap: tag error content for LLM visibility
        if tr.status == "error" and not tr.content.lstrip().startswith("<error>\n"):
            tr = tr.model_copy(update={"content": f"<error>\n{tr.content}\n</error>"})

        # Truncate
        max_chars = instance.tool_spec.max_result_chars
        if max_chars > 0 and len(tr.content) > max_chars:
            tr = self._truncate_result(tr, max_chars, tc.id)

        if self._hook_executor is not None:
            post_ctx = PostToolCallContext(
                tool_name=tc.name,
                tool_call_id=tc.id,
                arguments=copy.deepcopy(effective_args),
                result=tr,
                turn=batch_ctx.turn,
            )
            tr = await self._hook_executor.emit_rewrite(
                HookEvent.POST_TOOL_CALL, post_ctx, tr
            )
            post_ctx_final = PostToolCallContext(
                tool_name=tc.name,
                tool_call_id=tc.id,
                arguments=copy.deepcopy(effective_args),
                result=tr,
                turn=batch_ctx.turn,
            )
            await self._hook_executor.emit(HookEvent.POST_TOOL_CALL, post_ctx_final)

        results[idx] = (tc, tr)
        if on_result:
            await on_result(tc, tr)
