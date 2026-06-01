"""Compaction dispatch generators used by AgentKernel.

Each helper yields ``CompactionEvent`` items wrapping the call into
``ContextCompactor`` and the optional checkpoint sink.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from matmaster.core.hooks import CompactionContext, HookEvent
from matmaster.core.kernel_items import _KernelItem, _KernelState
from matmaster.types.events import CompactionEvent

if TYPE_CHECKING:
    from matmaster.context.sources.turn_input import TurnInput
    from matmaster.types.runtime import AgentKernelResources, AgentKernelSpec

logger = logging.getLogger(__name__)


async def run_compaction_plan(
    *,
    kernel_spec: AgentKernelSpec,
    kernel_resources: AgentKernelResources,
    state: _KernelState,
    plan: Any,
    checkpoint_sink: Any,
    turn_input: TurnInput | None = None,
    tool_definitions: list[dict[str, Any]] | None = None,
) -> AsyncIterator[_KernelItem]:
    """Run a compaction plan, emit start/complete events, persist checkpoint.

    ``turn_input`` is only consulted by ``apply_summary`` for
    preflight plans; runtime callers pass ``None``.
    """
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
    pre_compaction_barrier = kernel_resources.runtime_ports.pre_compaction_barrier
    if pre_compaction_barrier is not None:
        result = pre_compaction_barrier()
        if inspect.isawaitable(result):
            await result
    try:
        from matmaster.context.compaction import call_summary_llm

        summary = await call_summary_llm(
            llm_provider=kernel_resources.llm_provider,
            system_prompt=kernel_spec.system_prompt,
            full_messages=state.messages,
            phase=plan.phase,
            turn_input=turn_input,
            tool_definitions=tool_definitions,
            context_limit=kernel_spec.compaction.context_limit,
            reserved_summary_tokens=kernel_spec.compaction.reserved_summary_tokens,
            safety_margin_tokens=(kernel_spec.compaction.summary_safety_margin_tokens),
        )
        result = await kernel_resources.compactor.apply_summary(
            plan,
            state.messages,
            summary,
            turn_input=turn_input,
        )
    except Exception as exc:
        if plan.phase == "preflight":
            logger.warning(
                "Preflight compaction summary failed; aborting", exc_info=True
            )
            raise
        logger.warning(
            "Compaction #%d summary failed; falling back",
            plan.compaction_count,
            exc_info=True,
        )
        result = await kernel_resources.compactor.apply_fallback(
            plan,
            state.messages,
            failure_reason=str(exc),
        )

    # Compactor mutates state.messages in place. Reset the incremental pipeline
    # so the next provider payload is rebuilt from the compacted history.
    state.pipeline.reset()

    messages_after = len(state.messages)

    if kernel_resources.hook_executor is not None:
        await kernel_resources.hook_executor.emit(
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
        checkpoint_sink is not None
        and result.durability == "durable"
        and result.base_messages is not None
    )
    if should_checkpoint:
        try:
            payload = {
                "durability": result.durability,
                "strategy": result.strategy,
                "schema_version": "history_checkpoint.v1",
                "render_version": "user_context_render.v1",
                "user_instructions_text": result.user_instructions_text,
                "user_instructions_hash": result.user_instructions_hash,
            }
            if result.checkpoint_covered_until_event_id is not None:
                payload["covered_until_event_id"] = (
                    result.checkpoint_covered_until_event_id
                )
            covered_until_event_id = await checkpoint_sink(
                payload=payload,
                base_messages=result.base_messages,
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
    kernel_spec: AgentKernelSpec,
    kernel_resources: AgentKernelResources,
    state: _KernelState,
    history: list | None,
    turn_input: TurnInput | None,
    checkpoint_sink: Any,
    tool_definitions: list[dict[str, Any]] | None = None,
) -> AsyncIterator[_KernelItem]:
    """Plan + execute a preflight compaction before the first LLM turn."""
    if not kernel_resources.compactor:
        return
    preflight_planner = getattr(
        kernel_resources.compactor, "plan_preflight_compaction", None
    )
    if callable(preflight_planner):
        skip_preflight_for_empty_history = (
            turn_input is not None and turn_input.has_effective_input() and not history
        )
        plan = (
            None
            if skip_preflight_for_empty_history
            else preflight_planner(state.messages)
        )
        if plan is not None:
            async for item in run_compaction_plan(
                kernel_spec=kernel_spec,
                kernel_resources=kernel_resources,
                state=state,
                plan=plan,
                checkpoint_sink=checkpoint_sink,
                turn_input=turn_input,
                tool_definitions=tool_definitions,
            ):
                yield item


async def run_runtime_compaction_if_needed(
    *,
    kernel_spec: AgentKernelSpec,
    kernel_resources: AgentKernelResources,
    state: _KernelState,
    checkpoint_sink: Any,
    tool_definitions: list[dict[str, Any]] | None = None,
) -> AsyncIterator[_KernelItem]:
    """Plan + execute a runtime compaction between LLM turns when budget exceeded.

    Runtime plans do not receive ``turn_input``; see ``apply_summary``,
    which only branches on it for preflight plans.
    """
    if not kernel_resources.compactor:
        return
    runtime_planner = getattr(
        kernel_resources.compactor, "plan_runtime_compaction", None
    )
    if callable(runtime_planner):
        plan = await runtime_planner(
            state.messages,
            state.turn_usage,
            turn=state.turn,
        )
        if plan is not None:
            async for item in run_compaction_plan(
                kernel_spec=kernel_spec,
                kernel_resources=kernel_resources,
                state=state,
                plan=plan,
                checkpoint_sink=checkpoint_sink,
                tool_definitions=tool_definitions,
            ):
                yield item
