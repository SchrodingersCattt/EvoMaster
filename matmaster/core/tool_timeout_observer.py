"""POST_TOOL_CALL hook wiring for tool timeout observations."""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any

from matmaster.core.hooks import HookEvent, HookExecutor
from matmaster.types.run_metadata import RunIdentity
from matmaster.types.runtime_ports import ToolTimeoutNotice, ToolTimeoutObserver


def _tool_arguments_preview(arguments: dict[str, Any], *, max_chars: int = 1000) -> str:
    try:
        text = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(arguments)
    if len(text) > max_chars:
        return text[:max_chars] + "...<truncated>"
    return text


def install_tool_timeout_observer_hooks(
    *,
    hook_executor: HookExecutor,
    observer: ToolTimeoutObserver,
    run_identity: RunIdentity,
    logger: logging.Logger,
) -> None:
    """Invoke an observer for final POST_TOOL_CALL timeout results."""

    async def _observe_tool_timeout(ctx) -> None:
        result = getattr(ctx, "result", None)
        if getattr(result, "status", None) != "timeout":
            return
        notice = ToolTimeoutNotice(
            session_id=run_identity.session_id,
            task_id=run_identity.task_id,
            spawn_id=run_identity.spawn_id,
            tool_name=ctx.tool_name,
            tool_call_id=ctx.tool_call_id,
            turn=ctx.turn,
            result_content=getattr(result, "content", "") or "",
            arguments_preview=_tool_arguments_preview(ctx.arguments),
        )
        try:
            maybe_awaitable = observer(notice)
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable
        except Exception:
            logger.warning(
                "tool timeout observer failed session_id=%s task_id=%s tool=%s",
                notice.session_id,
                notice.task_id,
                notice.tool_name,
                exc_info=True,
            )

    hook_executor.on(HookEvent.POST_TOOL_CALL, _observe_tool_timeout)
