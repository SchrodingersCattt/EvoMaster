"""Tool-call dispatch loop + helpers used by AgentKernel."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any

from matmaster.core.kernel_items import _KernelItem
from matmaster.core.tool_runner import ToolExecutionContext
from matmaster.types.cancellation import CancellationToken
from matmaster.types.errors import LLMError
from matmaster.types.events import SkillHitEvent, ToolResultEvent
from matmaster.types.messages import ToolCallData, ToolMessage

if TYPE_CHECKING:
    from matmaster.core.kernel_items import _KernelState

logger = logging.getLogger(__name__)

AGENT_TOOL_NAME = "Agent"


class InvalidToolUsageDelta(RuntimeError):
    """Raised when an internal tool usage payload violates scalar usage shape."""


def validate_tool_call_ids(tool_calls: list[ToolCallData]) -> None:
    """Reject assembled responses that contain duplicate tool_call ids."""
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


def extract_tool_usage_delta(tool_name: str, tool_result: Any) -> dict[str, int]:
    """Extract a validated usage delta from a structured tool result."""
    if tool_name != AGENT_TOOL_NAME:
        return {}

    payload = getattr(tool_result, "payload", {}) or {}
    if "subagent_usage" not in payload:
        return {}

    usage = payload["subagent_usage"]
    if not isinstance(usage, dict):
        logger.warning(
            "malformed Agent subagent_usage: expected dict, got %s",
            type(usage).__name__,
        )
        raise InvalidToolUsageDelta("Agent subagent_usage must be a dict")

    out: dict[str, int] = {}
    for key, value in usage.items():
        if (
            not isinstance(key, str)
            or not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            logger.warning(
                "malformed Agent subagent_usage field=%r value_type=%s",
                key,
                type(value).__name__,
            )
            raise InvalidToolUsageDelta(
                "Agent subagent_usage values must be non-negative ints"
            )
        out[key] = value
    return out


async def dispatch_tool_calls(
    *,
    tool_calls: Sequence[ToolCallData],
    tool_runner: Any,
    max_turns: int,
    state: _KernelState,
    cancel_token: CancellationToken | None,
) -> AsyncIterator[_KernelItem]:
    """Execute the turn's tool calls, append tool messages, emit events."""
    if tool_runner is None:
        raise RuntimeError("No tool_runner in kernel resources")

    exec_ctx = ToolExecutionContext(
        turn=state.turn,
        max_turns=max_turns,
        cancel_token=cancel_token,
    )
    runner_results = await tool_runner.execute_batch(tool_calls, exec_ctx)

    for tc, tool_result in runner_results:
        state.messages.append(
            ToolMessage(
                tool_call_id=tc.id,
                tool_name=tc.name,
                content=tool_result.content,
                images=tool_result.images,
            )
        )
        usage_delta = extract_tool_usage_delta(tc.name, tool_result)
        if usage_delta:
            accumulate_usage(state.total_usage, usage_delta)
        yield _KernelItem(
            event=ToolResultEvent(
                source="agent",
                call_id=tc.id,
                tool_name=tc.name,
                result=tool_result.content,
                status=tool_result.status,
                payload=tool_result.payload,
                images=tool_result.images,
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
