"""ToolRunner Protocol and FullToolRunner implementation.

ToolRunner defines the execution strategy interface for tool calls.

FullToolRunner executes the Tool Runtime v2 pipeline:
Catalog -> StructuralValidation -> CapabilityPolicy -> fast path ->
Scheduler -> executor -> release.

ToolExecutionContext carries per-batch execution metadata (turn, max_turns,
stop_event).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from matmaster.core.structural_validation import StructuralValidation
from matmaster.core.tool_scheduler import SchedulerTicket, ToolScheduler
from matmaster.tools.tool_catalog import ToolCatalog
from matmaster.tools.tool_result import ToolResult, normalize_tool_result
from matmaster.types.messages import ToolCallData
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolExecutionContext as _ExecCtx, ToolInstance
from matmaster.types.topology import RuntimeTopology

if TYPE_CHECKING:
    from matmaster.core.capability_policy import CapabilityPolicy


@dataclass
class BatchExecutionContext:
    """Per-batch execution context used internally by FullToolRunner.

    Carries the current turn number and an optional stop event for cancellation.
    """

    turn: int
    max_turns: int
    stop_event: threading.Event | None = None
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

    Per D-01: Does not call pre_hook/post_hook.
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
        state: ToolRunnerState | None = None,
    ) -> None:
        self._catalog = catalog
        self._validation = structural_validation
        self._policy = capability_policy
        self._scheduler = scheduler
        self._topology = topology
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
        )

    async def execute_batch(
        self,
        tool_calls: list[ToolCallData],
        ctx: ToolExecutionContext,
        *,
        on_result: Callable[[ToolCallData, ToolResult], Awaitable[None]] | None = None,
    ) -> list[tuple[ToolCallData, ToolResult]]:
        """Two-phase tool execution.

        Phase 1 (serial): validate each call through the constraint layers.
        Phase 2 (concurrent): execute all approved calls via asyncio.gather.

        Returns list of (ToolCallData, ToolResult) in input order.
        """
        n = len(tool_calls)
        results: list[tuple[ToolCallData, ToolResult] | None] = [None] * n
        approved: list[tuple[int, ToolCallData, ToolInstance, dict[str, Any], bool]] = []

        # ── Phase 1: Serial validation ─────────────────────
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

            # 1b. Cancel check (stop_mode-aware)
            if ctx.stop_event is not None and ctx.stop_event.is_set():
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

            # 2. StructuralValidation (Layer A)
            decision = self._validation.validate(
                self._topology, instance, tc.arguments
            )
            if decision.decision == "deny":
                tr = ToolResult(
                    status="error",
                    content=decision.reason,
                    meta={"layer": "structural"},
                )
                results[idx] = (tc, tr)
                if on_result:
                    await on_result(tc, tr)
                continue

            effective_args = decision.modified_args or tc.arguments

            # 2b. input_validator
            if instance.input_validator is not None:
                try:
                    iv_decision = await instance.input_validator(
                        effective_args,
                        self._state,
                    )
                except Exception as exc:
                    tr = ToolResult(
                        status="error",
                        content=str(exc),
                        meta={"layer": "input_validation"},
                    )
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
                    results[idx] = (tc, tr)
                    if on_result:
                        await on_result(tc, tr)
                    continue

            # 3. CapabilityPolicy (Layer B)
            decision = self._policy.evaluate(self._topology, instance, effective_args)
            if decision.decision == "deny":
                tr = ToolResult(
                    status="error",
                    content=decision.reason,
                    meta={"layer": "policy", "guidance": decision.guidance},
                )
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

            approved.append((idx, tc, instance, effective_args, is_fast))

        # ── Phase 2: Concurrent execution ──────────────────
        if approved:
            exec_ctx = _ExecCtx(
                stop_event=ctx.stop_event,
                runner_state=self._state,
            )
            await asyncio.gather(
                *(
                    self._execute_one(
                        idx, tc, instance, effective_args, is_fast,
                        exec_ctx, ctx, results, on_result,
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
            tr = await instance.tool_executor(effective_args, exec_ctx)
        except Exception as e:
            tr = ToolResult.from_error(tc.name, e)
        finally:
            if ticket is not None:
                await self._scheduler.release(ticket)

        # Normalize
        tr = normalize_tool_result(tr)

        # Truncate
        max_chars = instance.tool_spec.max_result_chars
        if max_chars > 0 and len(tr.content) > max_chars:
            tr = self._truncate_result(tr, max_chars, tc.id)

        results[idx] = (tc, tr)
        if on_result:
            await on_result(tc, tr)
