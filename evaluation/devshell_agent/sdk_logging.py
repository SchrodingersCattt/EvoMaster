"""Shared SDK message logging helpers for the DevShell agent loop."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO


def log_line(msg: str, loop_log: TextIO) -> None:
    print(msg, file=sys.stderr, flush=True)
    loop_log.write(msg + "\n")
    loop_log.flush()


def truncate_log_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n… [log truncated, total_len={len(text)} chars]"


def _log_content_block_sdk(
    block: Any,
    *,
    prefix: str,
    loop_log: TextIO,
    tool_result_max_chars: int,
    text_block_max_chars: int,
) -> None:
    from claude_agent_sdk import (  # type: ignore[import-untyped]
        TextBlock,
        ThinkingBlock,
        ToolResultBlock,
        ToolUseBlock,
    )

    if isinstance(block, TextBlock):
        raw = block.text
        if not raw.strip():
            log_line(f"{prefix}:text (empty)", loop_log)
        else:
            log_line(
                f"{prefix}:text\n{truncate_log_text(raw, text_block_max_chars)}",
                loop_log,
            )
        return

    if isinstance(block, ThinkingBlock):
        sig = block.signature
        sig_short = f"{sig[:24]}…" if len(sig) > 24 else sig
        log_line(
            f"{prefix}:thinking chars={len(block.thinking)} "
            f"signature_prefix={sig_short!r}",
            loop_log,
        )
        if block.thinking.strip():
            log_line(
                f"{prefix}:thinking_body\n"
                f"{truncate_log_text(block.thinking, tool_result_max_chars)}",
                loop_log,
            )
        return

    if isinstance(block, ToolUseBlock):
        try:
            inp = json.dumps(block.input, ensure_ascii=False, default=str)
        except TypeError:
            inp = repr(block.input)
        inp = truncate_log_text(inp, tool_result_max_chars)
        log_line(
            f"{prefix}:tool_use id={block.id!r} name={block.name!r} input={inp}",
            loop_log,
        )
        return

    if isinstance(block, ToolResultBlock):
        c = block.content
        if c is None:
            body = "(none)"
        elif isinstance(c, list):
            try:
                body = json.dumps(c, ensure_ascii=False, default=str)
            except TypeError:
                body = repr(c)
        else:
            body = str(c)
        log_line(
            f"{prefix}:tool_result tool_use_id={block.tool_use_id!r} "
            f"is_error={block.is_error!r}",
            loop_log,
        )
        log_line(
            f"{prefix}:tool_result_body\n"
            f"{truncate_log_text(body, tool_result_max_chars)}",
            loop_log,
        )
        return

    log_line(f"{prefix}:unknown_block {type(block).__name__} {block!r}", loop_log)


def log_sdk_message(
    message: Any,
    *,
    loop_log: TextIO,
    tool_result_max_chars: int,
    stream_event_max_chars: int,
    text_block_max_chars: int,
    system_data_max_chars: int,
) -> None:
    """Log one Claude Agent SDK stream message (all known types)."""
    from claude_agent_sdk import (  # type: ignore[import-untyped]
        AssistantMessage,
        RateLimitEvent,
        ResultMessage,
        StreamEvent,
        SystemMessage,
        TaskNotificationMessage,
        TaskProgressMessage,
        TaskStartedMessage,
        UserMessage,
    )

    def j(x: Any) -> str:
        try:
            return json.dumps(x, ensure_ascii=False, default=str)
        except TypeError:
            return repr(x)

    if isinstance(message, UserMessage):
        log_line(
            f"[sdk:user] uuid={message.uuid!r} "
            f"parent_tool_use_id={message.parent_tool_use_id!r}",
            loop_log,
        )
        if message.tool_use_result is not None:
            log_line(
                "[sdk:user] tool_use_result="
                f"{truncate_log_text(j(message.tool_use_result), tool_result_max_chars)}",
                loop_log,
            )
        c = message.content
        if isinstance(c, str):
            if c.strip():
                log_line(
                    "[sdk:user:text]\n" f"{truncate_log_text(c, text_block_max_chars)}",
                    loop_log,
                )
            else:
                log_line("[sdk:user:text] (empty)", loop_log)
        else:
            for i, block in enumerate(c):
                _log_content_block_sdk(
                    block,
                    prefix=f"[sdk:user:block:{i}]",
                    loop_log=loop_log,
                    tool_result_max_chars=tool_result_max_chars,
                    text_block_max_chars=text_block_max_chars,
                )
        return

    if isinstance(message, AssistantMessage):
        parts = [
            f"model={message.model!r}",
            f"message_id={message.message_id!r}",
            f"uuid={message.uuid!r}",
            f"parent_tool_use_id={message.parent_tool_use_id!r}",
        ]
        if message.error:
            parts.append(f"error={message.error!r}")
        if message.stop_reason:
            parts.append(f"stop_reason={message.stop_reason!r}")
        if message.usage is not None:
            parts.append(f"usage={j(message.usage)}")
        log_line("[sdk:assistant] " + " ".join(parts), loop_log)
        for i, block in enumerate(message.content):
            _log_content_block_sdk(
                block,
                prefix=f"[sdk:assistant:block:{i}]",
                loop_log=loop_log,
                tool_result_max_chars=tool_result_max_chars,
                text_block_max_chars=text_block_max_chars,
            )
        return

    if isinstance(message, TaskStartedMessage):
        log_line(
            f"[sdk:system:task_started] subtype={message.subtype!r} "
            f"task_id={message.task_id!r} description={message.description!r} "
            f"uuid={message.uuid!r} session_id={message.session_id!r} "
            f"tool_use_id={message.tool_use_id!r} task_type={message.task_type!r}",
            loop_log,
        )
        return

    if isinstance(message, TaskProgressMessage):
        log_line(
            f"[sdk:system:task_progress] subtype={message.subtype!r} "
            f"task_id={message.task_id!r} description={message.description!r} "
            f"uuid={message.uuid!r} session_id={message.session_id!r} "
            f"tool_use_id={message.tool_use_id!r} "
            f"last_tool_name={message.last_tool_name!r} "
            f"usage={j(message.usage)}",
            loop_log,
        )
        return

    if isinstance(message, TaskNotificationMessage):
        log_line(
            f"[sdk:system:task_notification] subtype={message.subtype!r} "
            f"task_id={message.task_id!r} status={message.status!r} "
            f"output_file={message.output_file!r} summary={message.summary!r} "
            f"uuid={message.uuid!r} session_id={message.session_id!r} "
            f"tool_use_id={message.tool_use_id!r}",
            loop_log,
        )
        if message.usage is not None:
            log_line(
                f"[sdk:system:task_notification:usage] {j(message.usage)}",
                loop_log,
            )
        return

    if isinstance(message, SystemMessage):
        log_line(
            f"[sdk:system] subtype={message.subtype!r} "
            f"data={truncate_log_text(j(message.data), system_data_max_chars)}",
            loop_log,
        )
        return

    if isinstance(message, ResultMessage):
        log_line(
            f"[sdk:result] subtype={message.subtype!r} num_turns={message.num_turns} "
            f"duration_ms={message.duration_ms} duration_api_ms={message.duration_api_ms} "
            f"is_error={message.is_error} stop_reason={message.stop_reason!r} "
            f"total_cost_usd={message.total_cost_usd!r} session_id={message.session_id!r} "
            f"uuid={message.uuid!r}",
            loop_log,
        )
        if message.usage:
            log_line(f"[sdk:result:usage] {j(message.usage)}", loop_log)
        if message.model_usage:
            log_line(f"[sdk:result:model_usage] {j(message.model_usage)}", loop_log)
        if message.errors:
            log_line(f"[sdk:result:errors] {j(message.errors)}", loop_log)
        if message.permission_denials:
            log_line(
                "[sdk:result:permission_denials] "
                f"{truncate_log_text(j(message.permission_denials), tool_result_max_chars)}",
                loop_log,
            )
        if message.result:
            log_line(
                "[sdk:result:result]\n"
                f"{truncate_log_text(message.result, tool_result_max_chars)}",
                loop_log,
            )
        if message.structured_output is not None:
            log_line(
                "[sdk:result:structured_output] "
                f"{truncate_log_text(j(message.structured_output), tool_result_max_chars)}",
                loop_log,
            )
        return

    if isinstance(message, StreamEvent):
        ev = j(message.event)
        log_line(
            f"[sdk:stream_event] uuid={message.uuid!r} "
            f"session_id={message.session_id!r} "
            f"parent_tool_use_id={message.parent_tool_use_id!r} "
            f"event={truncate_log_text(ev, stream_event_max_chars)}",
            loop_log,
        )
        return

    if isinstance(message, RateLimitEvent):
        ri = message.rate_limit_info
        log_line(
            f"[sdk:rate_limit] uuid={message.uuid!r} session_id={message.session_id!r} "
            f"status={ri.status!r} rate_limit_type={ri.rate_limit_type!r} "
            f"resets_at={ri.resets_at!r} utilization={ri.utilization!r}",
            loop_log,
        )
        if ri.raw:
            log_line(
                f"[sdk:rate_limit:raw] {truncate_log_text(j(ri.raw), system_data_max_chars)}",
                loop_log,
            )
        return

    log_line(f"[sdk:unhandled] {type(message).__name__}: {message!r}", loop_log)
