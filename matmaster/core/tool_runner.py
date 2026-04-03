"""ToolRunner Protocol, InlineToolRunner, and FullToolRunner implementations.

ToolRunner defines the execution strategy interface for tool calls.

InlineToolRunner is the Phase 1 transition: wraps the current agent.py
guard -> pre_hook -> execute -> post_hook chain as a standalone ToolRunner.

FullToolRunner is the Phase 2 complete execution chain:
Catalog -> StructuralValidation -> RunStateGuard -> CapabilityPolicy ->
fast path -> Scheduler -> executor -> release.

ToolExecutionContext carries per-batch execution metadata (turn, max_turns,
stop_event).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from matmaster.core.guard_pipeline import GuardPipeline
from matmaster.core.hooks import (
    HookAction,
    run_guard_blocked,
    run_post_tool_call,
    run_pre_tool_call,
)
from matmaster.core.structural_validation import StructuralValidation
from matmaster.core.tool_scheduler import SchedulerTicket, ToolScheduler
from matmaster.tools.tool_catalog import ToolCatalog
from matmaster.tools.tool_result import ToolResult, normalize_tool_result
from matmaster.types.messages import ToolCallData
from matmaster.types.topology import RuntimeTopology

if TYPE_CHECKING:
    from matmaster.core.capability_policy import CapabilityPolicy
    from matmaster.core.hooks import Hook
    from matmaster.types.guards import Guard

logger = logging.getLogger(__name__)


@dataclass
class ToolExecutionContext:
    """Per-batch execution context passed to ToolRunner.

    Carries the current turn number, max turns for guard evaluation,
    and an optional stop event for cancellation.
    """

    turn: int
    max_turns: int
    stop_event: threading.Event | None = None


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


class InlineToolRunner:
    """DEPRECATED: Phase 1 transition runner. Use FullToolRunner instead.

    Retained for test compatibility. Will be removed in future cleanup.

    Original design: wraps agent.py guard->hook->execute->hook chain.
    The three-phase execution model is preserved:

    Phase 1 (serial): Guard evaluation + pre_hook gating
    Phase 2 (parallel): Concurrent tool execution via asyncio.gather
    Phase 3 (serial): Post-hook callbacks in original order
    """

    def __init__(
        self,
        spec: Any,  # AgentRuntimeSpec (avoid circular import)
        guards: list[Guard],
    ) -> None:
        self._spec = spec
        self._guard_pipeline = GuardPipeline(guards)

    async def execute_batch(
        self,
        tool_calls: list[ToolCallData],
        ctx: ToolExecutionContext,
        *,
        on_result: Callable[[ToolCallData, ToolResult], Awaitable[None]] | None = None,
    ) -> list[tuple[ToolCallData, ToolResult]]:
        """Execute a batch of tool calls through guard -> hook -> execute -> hook.

        Returns list of (ToolCallData, ToolResult) in input order.
        """
        hooks = self._spec.hooks
        results: list[tuple[ToolCallData, ToolResult]] = []

        # Phase 1: Serial guard + pre_hook gating
        approved: list[tuple[int, ToolCallData]] = []  # (result_index, tc)
        for tc in tool_calls:
            # Check stop_event between serial tool calls (cancel semantics)
            if ctx.stop_event is not None and ctx.stop_event.is_set():
                tr = ToolResult(status="cancelled", content="Run cancelled.")
                results.append((tc, tr))
                if on_result:
                    await on_result(tc, tr)
                continue

            guard_result = self._guard_pipeline.evaluate(tc, ctx.turn, ctx.max_turns)
            if not guard_result.allowed:
                await run_guard_blocked(hooks, tc, guard_result)
                blocked_content = f"BLOCKED: {guard_result.reason}"
                if guard_result.guidance:
                    blocked_content += f"\n{guard_result.guidance}"
                tr = ToolResult(status="blocked", content=blocked_content)
                results.append((tc, tr))
                if on_result:
                    await on_result(tc, tr)
                continue

            action = await run_pre_tool_call(hooks, tc)
            if action == HookAction.SKIP:
                tr = ToolResult(status="skipped", content="Tool call skipped by hook.")
                results.append((tc, tr))
                if on_result:
                    await on_result(tc, tr)
                continue

            idx = len(results)
            results.append((tc, ToolResult()))  # placeholder
            approved.append((idx, tc))

        # Phase 2: Parallel execution of approved tools
        if approved:

            async def _exec(tc: ToolCallData) -> ToolResult:
                try:
                    # Use tool_catalog.registry for tool lookup + execute
                    catalog = getattr(self._spec, 'tool_catalog', None)
                    if catalog is not None:
                        registry = catalog.registry
                    else:
                        raise RuntimeError("No tool_catalog in spec (InlineToolRunner is DEPRECATED)")
                    raw_tool = registry._tools.get(tc.name)
                    if raw_tool is None:
                        available = ", ".join(sorted(registry._tools))
                        return ToolResult(
                            status="error",
                            content=f"Error: Tool '{tc.name}' not found. Available: {available}",
                        )
                    result = await raw_tool.execute(tc.arguments)
                    return normalize_tool_result(result)
                except Exception as e:
                    logger.exception("Tool execution failed: %s", tc.name)
                    return ToolResult.from_error(tc.name, e)

            raw_results = await asyncio.gather(
                *[_exec(tc) for _, tc in approved],
                return_exceptions=True,
            )
            for (idx, tc), raw in zip(approved, raw_results):
                if isinstance(raw, BaseException):
                    tr = ToolResult.from_error(tc.name, raw)
                else:
                    tr = raw
                results[idx] = (tc, tr)

        # Phase 3: Post-hook in order (only for executed tools)
        executed_indices = {idx for idx, _ in approved}
        for i, (tc, tr) in enumerate(results):
            was_executed = i in executed_indices
            if was_executed and tr.status not in ("blocked", "skipped"):
                await run_post_tool_call(hooks, tc, tr)
                if on_result:
                    await on_result(tc, tr)

        return results


class FullToolRunner:
    """Complete ToolRunner: Catalog -> Validation -> Guard -> Policy -> Scheduler -> Execute -> Release.

    Per D-01: Does not call pre_hook/post_hook.
    Per D-05: Strictly follows spec section 9.1 execution chain.
    Per D-06: Each layer produces ToolResult with meta["layer"] marking failure source.
    """

    def __init__(
        self,
        catalog: ToolCatalog,
        structural_validation: StructuralValidation,
        guard_pipeline: GuardPipeline,
        capability_policy: CapabilityPolicy,
        scheduler: ToolScheduler,
        topology: RuntimeTopology,
    ) -> None:
        self._catalog = catalog
        self._validation = structural_validation
        self._guard_pipeline = guard_pipeline
        self._policy = capability_policy
        self._scheduler = scheduler
        self._topology = topology

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
        """Execute tool calls through the seven-step chain.

        For each tool_call, sequentially:
        1. Catalog lookup
        1b. Cancel check (stop_mode-aware: cancellable->cancel, best_effort->cancel, non_cancellable->skip)
        2. StructuralValidation (Layer A)
        3. RunStateGuard (Layer B) via GuardPipeline
        4. CapabilityPolicy (Layer C)
        5. Fast path check
        6. Scheduler acquire (skipped for fast path)
        7. Execute + Release
        8. Normalize + Truncate

        Returns list of (ToolCallData, ToolResult) in input order.
        """
        results: list[tuple[ToolCallData, ToolResult]] = []

        for tc in tool_calls:
            # 1 + 2. Catalog lookup first, then stop_mode-aware cancel check
            instance = self._catalog.get_tool(tc.name)
            if instance is None:
                tr = ToolResult(
                    status="error",
                    content=f"Unknown tool: {tc.name}",
                    meta={"layer": "catalog"},
                )
                results.append((tc, tr))
                if on_result:
                    await on_result(tc, tr)
                continue

            # 1b. Cancel check (stop_mode-aware, after catalog lookup)
            if ctx.stop_event is not None and ctx.stop_event.is_set():
                stop_mode = instance.tool_binding.stop_mode
                if stop_mode == "cancellable":
                    tr = ToolResult(status="cancelled", content="Run cancelled.")
                    results.append((tc, tr))
                    if on_result:
                        await on_result(tc, tr)
                    continue
                elif stop_mode == "best_effort":
                    tr = ToolResult(
                        status="cancelled",
                        content="Cancellation requested (best-effort). Tool may have partially completed.",
                    )
                    results.append((tc, tr))
                    if on_result:
                        await on_result(tc, tr)
                    continue
                # stop_mode == "non_cancellable": skip cancel, let tool execute

            # 3. StructuralValidation (Layer A)
            decision = self._validation.validate(
                self._topology, instance, tc.arguments
            )
            if decision.decision == "deny":
                tr = ToolResult(
                    status="error",
                    content=decision.reason,
                    meta={"layer": "structural"},
                )
                results.append((tc, tr))
                if on_result:
                    await on_result(tc, tr)
                continue

            # 3b. input_validator (tool-specific semantic check)
            if instance.input_validator is not None:
                try:
                    iv_decision = await instance.input_validator(tc.arguments)
                except Exception as exc:
                    tr = ToolResult(
                        status="error",
                        content=str(exc),
                        meta={"layer": "input_validation"},
                    )
                    results.append((tc, tr))
                    if on_result:
                        await on_result(tc, tr)
                    continue
                if iv_decision is not None and iv_decision.decision == "deny":
                    tr = ToolResult(
                        status="error",
                        content=iv_decision.reason,
                        meta={"layer": "input_validation"},
                    )
                    results.append((tc, tr))
                    if on_result:
                        await on_result(tc, tr)
                    continue

            # 4. RunStateGuard (Layer B)
            guard_result = self._guard_pipeline.evaluate(tc, ctx.turn, ctx.max_turns)
            if not guard_result.allowed:
                tr = ToolResult(
                    status="error",
                    content=guard_result.reason or "Guard denied",
                    meta={"layer": "guard"},
                )
                results.append((tc, tr))
                if on_result:
                    await on_result(tc, tr)
                continue

            # 5. CapabilityPolicy (Layer C)
            decision = self._policy.evaluate(self._topology, instance, tc.arguments)
            if decision.decision == "deny":
                tr = ToolResult(
                    status="error",
                    content=decision.reason,
                    meta={"layer": "policy", "guidance": decision.guidance},
                )
                results.append((tc, tr))
                if on_result:
                    await on_result(tc, tr)
                continue

            # 6. Fast path check (per D-08)
            claims = instance.tool_binding.resource_claims
            is_fast = (
                instance.tool_spec.effect_level == "none"
                and all(c.mode == "shared_read" for c in claims)
                and instance.tool_spec.fast_path_eligible
            )

            # 7. Scheduler acquire (skip for fast path)
            ticket: SchedulerTicket | None = None
            if not is_fast:
                ticket = await self._scheduler.acquire(
                    claims, timeout=self._scheduler._default_timeout
                )
                if ticket is None:
                    tr = ToolResult(
                        status="error",
                        content="Scheduling timeout",
                        meta={"layer": "scheduler"},
                    )
                    results.append((tc, tr))
                    if on_result:
                        await on_result(tc, tr)
                    continue

            # 8. Execute + Release
            try:
                tr = await instance.tool_executor(tc.arguments)
            except Exception as e:
                tr = ToolResult.from_error(tc.name, e)
            finally:
                if ticket is not None:
                    await self._scheduler.release(ticket)

            # 9a. Normalize (builtins may return str or None)
            tr = normalize_tool_result(tr)

            # 9b. Truncate oversized content
            max_chars = instance.tool_spec.max_result_chars
            if max_chars > 0 and len(tr.content) > max_chars:
                tr = self._truncate_result(tr, max_chars, tc.id)

            results.append((tc, tr))
            if on_result:
                await on_result(tc, tr)

        return results
