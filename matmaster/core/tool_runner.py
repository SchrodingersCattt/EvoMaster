"""ToolRunner Protocol and InlineToolRunner transition implementation.

ToolRunner defines the execution strategy interface for tool calls.
InlineToolRunner is the Phase 1 transition: wraps the current agent.py
guard -> pre_hook -> execute -> post_hook chain as a standalone ToolRunner.

ToolExecutionContext carries per-batch execution metadata (turn, max_turns,
stop_event).

Phase 2 (Plan 33) will implement the full ToolRunner with
ToolCatalog lookup -> StructuralValidation -> RunStateGuard ->
CapabilityPolicy -> ToolScheduler -> executor -> release.
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
from matmaster.tools.tool_result import ToolResult
from matmaster.types.messages import ToolCallData

if TYPE_CHECKING:
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
    """Phase 1 transition: wraps current agent.py guard->hook->execute->hook chain.

    Extracts the logic from agent.py L217-311 into a standalone ToolRunner.
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
                    return await self._spec.tool_registry.execute(tc.name, tc.arguments)
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
