"""Unified hook dispatch for the matmaster runtime.

The hook system centers on HookExecutor with three capabilities:
- observe: fire-and-forget event observation
- intercept: parallel veto checks with aggregated results
- rewrite: serial transformation chains
"""

from __future__ import annotations

import asyncio
import enum
import logging
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

from matmaster.tools.tool_result import ToolResult

logger = logging.getLogger(__name__)

ObserveHandler = Callable[[Any], Awaitable[None]]
InterceptHandler = Callable[[Any], Awaitable["HookResult"]]
RewriteHandler = Callable[[Any, Any], Awaitable[Any]]
T = TypeVar("T")


def _clone_hook_value(value: T) -> T:
    """Return an isolated snapshot for hook execution."""
    return deepcopy(value)


class HookEvent(str, enum.Enum):
    RUN_START = "run_start"
    RUN_END = "run_end"
    PRE_TOOL_CALL = "pre_tool_call"
    POST_TOOL_CALL = "post_tool_call"
    SUBAGENT_START = "subagent_start"
    SUBAGENT_STOP = "subagent_stop"
    CONTEXT_COMPACTION = "context_compaction"
    USER_PROMPT_SUBMIT = "user_prompt_submit"


class HookOutcome(str, enum.Enum):
    SUCCESS = "success"
    BLOCK = "block"
    ERROR = "error"


@dataclass
class HookResult:
    outcome: HookOutcome = HookOutcome.SUCCESS
    message: str = ""
    data: Any = None


@dataclass(frozen=True)
class RunContext:
    task_id: str
    session_id: str
    reason: str


@dataclass(frozen=True)
class PreToolCallContext:
    tool_name: str
    tool_call_id: str
    arguments: dict[str, Any]
    turn: int


@dataclass(frozen=True)
class PostToolCallContext:
    tool_name: str
    tool_call_id: str
    arguments: dict[str, Any]
    result: ToolResult
    turn: int


@dataclass(frozen=True)
class SubagentContext:
    agent_id: str
    agent_type: str
    parent_session_id: str
    task_preview: str = ""


@dataclass(frozen=True)
class CompactionContext:
    messages_before: int
    messages_after: int
    trigger_tokens: int
    strategy: str


@dataclass(frozen=True)
class UserPromptContext:
    prompt: str
    session_id: str


class HookExecutor:
    """Unified hook dispatch: observe, intercept, and rewrite."""

    def __init__(self) -> None:
        self._observers: dict[HookEvent, list[ObserveHandler]] = defaultdict(list)
        self._interceptors: dict[HookEvent, list[InterceptHandler]] = defaultdict(list)
        self._rewriters: dict[HookEvent, list[RewriteHandler]] = defaultdict(list)

    def on(self, event: HookEvent, handler: ObserveHandler) -> None:
        self._observers[event].append(handler)

    def intercept(self, event: HookEvent, handler: InterceptHandler) -> None:
        self._interceptors[event].append(handler)

    def rewrite(self, event: HookEvent, handler: RewriteHandler) -> None:
        self._rewriters[event].append(handler)

    async def emit(self, event: HookEvent, ctx: Any) -> None:
        handlers = self._observers.get(event, [])
        if not handlers:
            return
        results = await asyncio.gather(
            *(handler(_clone_hook_value(ctx)) for handler in handlers),
            return_exceptions=True,
        )
        for index, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.warning("Hook %s raised: %s", handlers[index], result)

    async def emit_intercept(self, event: HookEvent, ctx: Any) -> HookResult:
        handlers = self._interceptors.get(event, [])
        if not handlers:
            return HookResult()

        results = await asyncio.gather(
            *(
                self._safe_intercept(handler, _clone_hook_value(ctx))
                for handler in handlers
            )
        )
        blocks = [result for result in results if result.outcome == HookOutcome.BLOCK]
        if blocks:
            message = "; ".join(block.message for block in blocks if block.message)
            return HookResult(outcome=HookOutcome.BLOCK, message=message)
        return HookResult()

    async def emit_rewrite(self, event: HookEvent, ctx: Any, data: T) -> T:
        for handler in self._rewriters.get(event, []):
            try:
                modified = await handler(
                    _clone_hook_value(ctx),
                    _clone_hook_value(data),
                )
                if modified is not None:
                    data = modified
            except Exception as exc:
                logger.warning("Rewrite hook %s raised: %s", handler, exc)
        return data

    async def _safe_intercept(self, handler: InterceptHandler, ctx: Any) -> HookResult:
        try:
            return await handler(ctx)
        except Exception as exc:
            logger.warning("Intercept hook %s raised: %s", handler, exc)
            return HookResult(outcome=HookOutcome.ERROR, message=str(exc))


__all__ = [
    "CompactionContext",
    "HookEvent",
    "HookExecutor",
    "HookOutcome",
    "HookResult",
    "PostToolCallContext",
    "PreToolCallContext",
    "RunContext",
    "SubagentContext",
    "UserPromptContext",
]
