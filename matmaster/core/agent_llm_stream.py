"""LLM streaming, chunk aggregation, and retry/backoff for AgentKernel."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from matmaster.core.finish_diagnostics import is_incomplete_response
from matmaster.core.kernel_items import _KernelItem, _KernelStopRequested
from matmaster.response_text import (
    is_empty_response_sentinel_prefix,
    is_trivial_response_text,
    normalize_visible_response_text,
)
from matmaster.types.cancellation import CancellationToken
from matmaster.types.errors import LLMError
from matmaster.types.events import ResponseEvent, ThoughtEvent
from matmaster.types.messages import LLMResponse, ToolCallData, parse_tool_arguments

if TYPE_CHECKING:
    from matmaster.types.runtime import AgentKernelResources

logger = logging.getLogger(__name__)

_STOP_CHECK_EVERY_N_STREAM_CHUNKS = 8
_STOP_RETRY_SLEEP_SLICE_SEC = 0.25


def _response_item(
    content: str, stream_id: str, stream_state: str | None
) -> _KernelItem:
    return _KernelItem(
        event=ResponseEvent(
            source="agent",
            content=content,
            stream_state=stream_state,
            stream_id=stream_id,
        )
    )


def _thought_item(
    reasoning: str, stream_id: str, stream_state: str | None
) -> _KernelItem:
    return _KernelItem(
        event=ThoughtEvent(
            source="agent",
            content=reasoning,
            stream_state=stream_state,
            stream_id=stream_id,
            reasoning_content=reasoning or None,
        )
    )


async def _sleep_backoff_with_cancel(
    seconds: float,
    cancel_token: CancellationToken | None,
) -> None:
    """Async sleep for *seconds*, but wake early if cancel_token is set."""
    if seconds <= 0:
        return
    if not cancel_token:
        await asyncio.sleep(seconds)
        return
    if await cancel_token.wait_async(seconds):
        raise _KernelStopRequested()


async def stream_llm_items(
    kernel_resources: AgentKernelResources,
    api_messages: list[dict[str, Any]],
    tool_defs: list[dict[str, Any]] | None,
    *,
    timeout: float | None = None,
    cancel_token: CancellationToken | None = None,
) -> AsyncIterator[_KernelItem]:
    """Sub-generator: streams LLM chunks as _KernelItem events.

    Replaces the EventEmitterHook path with direct yields of
    ThoughtEvent/ResponseEvent. Final yield carries the assembled
    LLMResponse. Keeps all accumulation logic from the streaming LLM call.
    """
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls_acc: dict[int, dict[str, str]] = {}
    finish_reason: str | None = None
    stream_id = f"turn-{len(api_messages)}"
    usage: dict[str, int] = {}
    usage_vendor: dict[str, Any] | None = None
    producing_reasoning = False
    producing_content = False
    pending_response_parts: list[str] = []
    response_stream_released = False

    # Start marker
    yield _thought_item("", stream_id, "start")

    stream_cancelled = False
    chunk_idx = 0
    t_stream0 = time.perf_counter()
    ttft_ms: float | None = None
    try:
        async for chunk in kernel_resources.llm_provider.chat_stream(
            api_messages, tool_defs, timeout=timeout
        ):
            if (
                cancel_token
                and chunk_idx % _STOP_CHECK_EVERY_N_STREAM_CHUNKS == 0
                and cancel_token.is_cancelled
            ):
                stream_cancelled = True
                break
            chunk_idx += 1
            if ttft_ms is None and (
                chunk.content or chunk.reasoning_content or chunk.tool_call_deltas
            ):
                ttft_ms = (time.perf_counter() - t_stream0) * 1000.0

            # Yield streaming events for reasoning and content chunks
            if chunk.reasoning_content:
                yield _thought_item(chunk.reasoning_content, stream_id, "streaming")

            if chunk.content:
                if response_stream_released:
                    yield _response_item(chunk.content, stream_id, "streaming")
                else:
                    pending_response_parts.append(chunk.content)
                    pending_content = "".join(pending_response_parts)
                    if not is_empty_response_sentinel_prefix(pending_content):
                        response_stream_released = True
                        pending_response_parts.clear()
                        yield _response_item(pending_content, stream_id, "streaming")

            # Accumulate parts (standard streaming accumulation)
            if chunk.reasoning_content:
                reasoning_parts.append(chunk.reasoning_content)
                producing_reasoning = True

            if chunk.content:
                # Segment transition: reasoning -> content
                if producing_reasoning:
                    yield _thought_item(
                        "".join(reasoning_parts), stream_id, "complete"
                    )
                    producing_reasoning = False
                content_parts.append(chunk.content)
                producing_content = True

            if chunk.finish_reason:
                finish_reason = chunk.finish_reason
            if chunk.usage:
                usage = chunk.usage
            if chunk.usage_vendor is not None:
                usage_vendor = chunk.usage_vendor
            if chunk.tool_call_deltas:
                # Segment transition: reasoning -> tool_calls
                if producing_reasoning:
                    yield _thought_item(
                        "".join(reasoning_parts), stream_id, "complete"
                    )
                    producing_reasoning = False
                # Segment transition: content -> tool_calls
                if producing_content:
                    content_snapshot = "".join(content_parts)
                    visible_snapshot = normalize_visible_response_text(content_snapshot)
                    if visible_snapshot is not None:
                        if pending_response_parts and not response_stream_released:
                            response_stream_released = True
                            pending_response_parts.clear()
                            yield _response_item(
                                visible_snapshot, stream_id, "streaming"
                            )
                        if not is_trivial_response_text(visible_snapshot):
                            yield _response_item(
                                visible_snapshot, stream_id, "segment_end"
                            )
                    else:
                        pending_response_parts.clear()
                    producing_content = False
                for delta in chunk.tool_call_deltas:
                    idx = delta.get("index", 0)
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": "",
                            "name": "",
                            "arguments": "",
                        }
                    if delta.get("id"):
                        tool_calls_acc[idx]["id"] = delta["id"]
                    if delta.get("name"):
                        tool_calls_acc[idx]["name"] = delta["name"]
                    if delta.get("arguments"):
                        tool_calls_acc[idx]["arguments"] += delta["arguments"]
    finally:
        # Emit segment-complete for any in-progress segments
        if producing_reasoning:
            yield _thought_item("".join(reasoning_parts), stream_id, "complete")
        if producing_content:
            content_snapshot = "".join(content_parts)
            visible_snapshot = normalize_visible_response_text(content_snapshot)
            if visible_snapshot is not None:
                if pending_response_parts and not response_stream_released:
                    response_stream_released = True
                    pending_response_parts.clear()
                    yield _response_item(visible_snapshot, stream_id, "streaming")
                yield _response_item(visible_snapshot, stream_id, "segment_end")
            else:
                pending_response_parts.clear()
        # End marker
        yield _response_item("", stream_id, "end")

    if stream_cancelled:
        raise _KernelStopRequested()

    total_stream_ms = (time.perf_counter() - t_stream0) * 1000.0
    joined_content = "".join(content_parts)
    joined_reasoning = "".join(reasoning_parts)
    logger.info(
        "LLM stream timing (generator): stream_id=%s api_messages=%d chunks=%d "
        "ttft_ms=%s total_ms=%.1f content_chars=%d reasoning_chars=%d has_tool_calls=%s",
        stream_id,
        len(api_messages),
        chunk_idx,
        f"{ttft_ms:.1f}" if ttft_ms is not None else "n/a",
        total_stream_ms,
        len(joined_content),
        len(joined_reasoning),
        bool(tool_calls_acc),
    )

    # Assemble tool_calls from accumulated deltas
    tool_calls: list[ToolCallData] | None = None
    if tool_calls_acc:
        tool_calls = []
        for _, v in sorted(tool_calls_acc.items()):
            args = parse_tool_arguments(v["arguments"])
            tool_calls.append(ToolCallData(id=v["id"], name=v["name"], arguments=args))
        if is_trivial_response_text(joined_content):
            joined_content = ""
    visible_content = normalize_visible_response_text(joined_content)

    yield _KernelItem(
        llm_response=LLMResponse(
            content=visible_content,
            reasoning_content=joined_reasoning or None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            usage_vendor=usage_vendor,
        )
    )


async def call_llm_streaming(
    kernel_resources: AgentKernelResources,
    api_messages: list[dict[str, Any]],
    tool_defs: list[dict[str, Any]] | None,
    *,
    cancel_token: CancellationToken | None = None,
) -> AsyncIterator[_KernelItem]:
    """Retry wrapper around _stream_llm_items with timeout-doubling retry on transient errors."""
    provider = kernel_resources.llm_provider
    current_timeout = getattr(provider, "stream_timeout", None) or getattr(
        provider, "_timeout", 300.0
    )
    max_retries = getattr(provider, "max_retries", 3)
    retry_delay = getattr(provider, "retry_delay", 1.0)

    last_error: LLMError | None = None
    for attempt in range(max_retries):
        if cancel_token and cancel_token.is_cancelled:
            raise _KernelStopRequested()
        t0 = time.monotonic()
        try:
            # Collect all items to handle incomplete-response retry
            items: list[_KernelItem] = []
            async for item in stream_llm_items(
                kernel_resources,
                api_messages,
                tool_defs,
                timeout=current_timeout,
                cancel_token=cancel_token,
            ):
                items.append(item)
                # Yield event items immediately (streaming)
                if item.event is not None:
                    yield item

            # Check for incomplete responses that should be retried before
            # the turn is allowed to terminate as invalid_finish.
            final_items = [i for i in items if i.llm_response is not None]
            if final_items:
                resp = final_items[0].llm_response
                elapsed = time.monotonic() - t0
                if is_incomplete_response(resp) and attempt < max_retries - 1:
                    logger.warning(
                        "LLM returned no visible final output "
                        "(attempt %d/%d, elapsed=%.1fs), retrying.",
                        attempt + 1,
                        max_retries,
                        elapsed,
                    )
                    backoff = retry_delay * (2**attempt)
                    await _sleep_backoff_with_cancel(backoff, cancel_token)
                    continue

                if is_incomplete_response(resp):
                    logger.warning(
                        "LLM returned incomplete response after %d attempts, "
                        "letting finish validation fail the turn.",
                        max_retries,
                    )

                yield _KernelItem(llm_response=resp)
                return

        except LLMError as e:
            elapsed = time.monotonic() - t0
            if not e.retryable:
                raise
            last_error = e
            current_timeout = current_timeout * 2
            backoff = retry_delay * (2**attempt) if attempt < max_retries - 1 else 0.0
            logger.warning(
                "LLM call failed (attempt %d/%d): %s (backoff=%.1fs)",
                attempt + 1,
                max_retries,
                e,
                backoff,
            )
            if attempt < max_retries - 1:
                await _sleep_backoff_with_cancel(backoff, cancel_token)

    # Retries exhausted
    if last_error is not None:
        raise LLMError(
            f"LLM stream failed after {max_retries} attempts: {last_error}",
            retryable=False,
            error_category=last_error.error_category,
        ) from last_error
    raise LLMError(
        f"LLM stream failed after {max_retries} attempts",
        retryable=False,
        error_category="incomplete_response",
    )
