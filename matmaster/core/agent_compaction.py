"""Compaction dispatch helpers extracted from AgentKernel.

Phase 0 refactor: these were inline methods on AgentKernel
(``_run_compaction_plan``, plus the inline preflight/runtime dispatch
blocks in ``_run_items``). They live here as free async generators so
that ``agent.py`` stays under the 800-line target and ``matmaster/context/``
work in later phases has room to grow.

Zero behavior change vs the pre-Phase-0 code paths.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from matmaster.core.hooks import CompactionContext, HookEvent
from matmaster.core.kernel_items import _KernelItem, _KernelState
from matmaster.types.current_input import CurrentInputContext
from matmaster.types.events import CompactionEvent

if TYPE_CHECKING:
    from matmaster.types.runtime import AgentRuntimeSpec

logger = logging.getLogger(__name__)


async def run_compaction_plan(
    *,
    spec: "AgentRuntimeSpec",
    state: _KernelState,
    plan: Any,
    checkpoint_sink: Any,
    current_input_context: CurrentInputContext | None = None,
) -> AsyncIterator[_KernelItem]:
    """Verbatim move of AgentKernel._run_compaction_plan."""
    yield _KernelItem(
        event=CompactionEvent(
            source="context_compactor",
            compaction_id=plan.compaction_id,
            status="running",
            phase=plan.phase,
            trigger_tokens=plan.trigger_tokens,
        )
    )
    messages_before = len(state.messages)
    pre_compaction_barrier = spec.runtime_ports.pre_compaction_barrier
    if callable(pre_compaction_barrier):
        result = pre_compaction_barrier()
        if inspect.isawaitable(result):
            await result
    result = await spec.compactor.apply_compaction_plan(
        plan,
        state.messages,
        current_input_context=current_input_context,
    )
    messages_after = len(state.messages)

    if spec.hook_executor is not None:
        await spec.hook_executor.emit(
            HookEvent.CONTEXT_COMPACTION,
            CompactionContext(
                messages_before=messages_before,
                messages_after=messages_after,
                trigger_tokens=result.trigger_tokens,
                strategy=result.strategy,
            ),
        )

    checkpoint_written = False
    failure_reason = result.failure_reason
    covered_until_event_id = None
    should_checkpoint = (
        callable(checkpoint_sink)
        and result.durability == "durable"
        and result.base_snapshot is not None
    )
    if should_checkpoint:
        try:
            payload = {
                "durability": result.durability,
                "strategy": result.strategy,
            }
            if result.checkpoint_covered_until_event_id is not None:
                payload["covered_until_event_id"] = (
                    result.checkpoint_covered_until_event_id
                )
            covered_until_event_id = await checkpoint_sink(
                payload=payload,
                base_messages=result.base_snapshot,
            )
        except Exception as exc:
            failure_reason = str(exc)
            logger.warning(
                "checkpoint sink failed for compaction result strategy=%s",
                result.strategy,
                exc_info=True,
            )
        else:
            checkpoint_written = True

    yield _KernelItem(
        event=CompactionEvent(
            source="context_compactor",
            compaction_id=result.compaction_id,
            status="complete",
            phase=result.phase,
            strategy=result.strategy,
            durability=result.durability,
            trigger_tokens=result.trigger_tokens,
            retained_turns=result.retained_turns,
            checkpoint_written=checkpoint_written,
            failure_reason=failure_reason,
            covered_until_event_id=covered_until_event_id,
        )
    )


async def run_preflight_compaction_if_needed(
    *,
    spec: "AgentRuntimeSpec",
    state: _KernelState,
    history: list | None,
    current_input_context: CurrentInputContext | None,
    checkpoint_sink: Any,
) -> AsyncIterator[_KernelItem]:
    """Verbatim move of the inline preflight dispatch."""
    if not spec.compactor:
        return
    spec.compactor.update_message_count(len(state.messages))
    preflight_planner = getattr(
        spec.compactor, "plan_preflight_compaction", None
    )
    if callable(preflight_planner):
        skip_preflight_for_empty_history = (
            current_input_context is not None
            and current_input_context.has_effective_input()
            and not history
        )
        plan = (
            None
            if skip_preflight_for_empty_history
            else preflight_planner(state.messages)
        )
        if plan is not None:
            async for item in run_compaction_plan(
                spec=spec,
                state=state,
                plan=plan,
                checkpoint_sink=checkpoint_sink,
                current_input_context=current_input_context,
            ):
                yield item
    else:
        await spec.compactor.preflight_if_needed(state.messages)


async def run_runtime_compaction_if_needed(
    *,
    spec: "AgentRuntimeSpec",
    state: _KernelState,
    turn_usage: dict,
    checkpoint_sink: Any,
) -> AsyncIterator[_KernelItem]:
    """Verbatim move of the inline runtime-compaction dispatch."""
    if not spec.compactor:
        return
    runtime_planner = getattr(
        spec.compactor, "plan_runtime_compaction", None
    )
    if callable(runtime_planner):
        plan = await runtime_planner(
            state.messages,
            turn_usage,
            turn=state.turn,
        )
        if plan is not None:
            async for item in run_compaction_plan(
                spec=spec,
                state=state,
                plan=plan,
                checkpoint_sink=checkpoint_sink,
            ):
                yield item
    else:
        await spec.compactor.compact_if_needed(
            state.messages, turn_usage, state.turn
        )
