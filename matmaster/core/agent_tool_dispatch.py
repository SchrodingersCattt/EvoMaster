"""Tool dispatch helpers extracted from AgentKernel.

Phase 0 refactor: the inline tool-call loop in ``AgentKernel._run_items``
(execute_batch + ToolMessage append + ToolResultEvent emit + SkillHitEvent
emit) and two adjacent static helpers (``_validate_tool_call_ids``,
``_accumulate_usage``) move here so ``agent.py`` stays under 800 lines.

Zero behavior change vs the pre-Phase-0 code paths.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING

from matmaster.core.kernel_items import _KernelItem
from matmaster.core.tool_runner import ToolExecutionContext
from matmaster.types.cancellation import CancellationToken
from matmaster.types.errors import LLMError
from matmaster.types.events import SkillHitEvent, ToolResultEvent
from matmaster.types.messages import ToolCallData, ToolMessage

if TYPE_CHECKING:
    from matmaster.core.kernel_items import _KernelState
    from matmaster.types.runtime import AgentRuntimeSpec


def validate_tool_call_ids(tool_calls: list[ToolCallData]) -> None:
    """Verbatim move of AgentKernel._validate_tool_call_ids."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for tc in tool_calls:
        if tc.id in seen:
            duplicates.append(tc.id)
        else:
            seen.add(tc.id)
    if duplicates:
        raise LLMError(
            f"duplicate tool_call ids in assembled response: {sorted(set(duplicates))}",
            retryable=False,
            error_category="bad_request",
        )


def accumulate_usage(total: dict[str, int], delta: dict[str, int]) -> None:
    """Accumulate per-turn usage into running total."""
    for k, v in delta.items():
        total[k] = total.get(k, 0) + v


async def dispatch_tool_calls(
    *,
    spec: AgentRuntimeSpec,
    state: _KernelState,
    tool_calls: Sequence[ToolCallData],
    turn_usage: dict,
    turn_index: int,
    cancel_token: CancellationToken | None,
) -> AsyncIterator[_KernelItem]:
    """Verbatim move of the inline tool-dispatch loop."""
    if spec.tool_runner is None:
        raise RuntimeError("No tool_runner in AgentRuntimeSpec")

    exec_ctx = ToolExecutionContext(
        turn=state.turn,
        max_turns=spec.max_turns,
        cancel_token=cancel_token,
    )
    runner_results = await spec.tool_runner.execute_batch(tool_calls, exec_ctx)

    for tc, tool_result in runner_results:
        state.messages.append(
            ToolMessage(
                tool_call_id=tc.id,
                tool_name=tc.name,
                content=tool_result.content,
            )
        )
        yield _KernelItem(
            event=ToolResultEvent(
                source="agent",
                call_id=tc.id,
                tool_name=tc.name,
                result=tool_result.content,
                status=tool_result.status,
                payload=tool_result.payload,
                turn_index=turn_index,
                turn_usage=dict(turn_usage),
                total_usage=dict(state.total_usage),
            )
        )
        if tc.name == "Skill":
            skill_name = tc.arguments.get("skill")
            if isinstance(skill_name, str) and skill_name:
                yield _KernelItem(
                    event=SkillHitEvent(
                        source="agent",
                        skill_name=skill_name,
                    )
                )
