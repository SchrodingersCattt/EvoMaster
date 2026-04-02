"""AgentKernel -- pure async execution loop for the agent kernel.

Consumes an AgentRuntimeSpec and executes the LLM -> guard -> hook -> tool
-> message accumulate -> loop cycle. Three-layer interface:

  _run_items()   -- private AsyncGenerator yielding _KernelItem
  run_stream()   -- public AsyncGenerator yielding BusEvent
  run()          -- public coroutine returning KernelRunResult (backward compat)

All three consume the same _run_items() generator, ensuring a single
execution path with no behavioral divergence.

Termination conditions:
- natural: LLM returns no tool_calls
- max_turns: turn counter reaches spec.max_turns
- cancelled: stop_event is set (checked each turn, during stream chunks, retry
  backoff, and between serial tool_calls)
- hook_stopped: should_continue hook returns False
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field as dc_field
from typing import TYPE_CHECKING, Any

from matmaster.core.guard_pipeline import GuardPipeline
from matmaster.tools.tool_result import ToolResult
from matmaster.types.errors import LLMError
from matmaster.types.events import (
    ResponseEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
)

if TYPE_CHECKING:
    from matmaster.types.runtime import AgentRuntimeSpec, KernelRunResult

from matmaster.core.hooks import (
    HookAction,
    run_guard_blocked,
    run_on_segment_complete,
    run_on_stream_chunk,
    run_post_tool_call,
    run_pre_llm_call,
    run_pre_tool_call,
    run_should_continue,
)
from matmaster.types.messages import (
    AssistantMessage,
    LLMResponse,
    Message,
    StreamChunk,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
    parse_tool_arguments,
)

logger = logging.getLogger(__name__)

# 流式输出中每隔 N 个 chunk 检查一次 stop_event（避免每 chunk 打 Redis EXISTS）
_STOP_CHECK_EVERY_N_STREAM_CHUNKS = 8
# 重试退避时切片 sleep 的步长（秒），便于尽快响应停止
_STOP_RETRY_SLEEP_SLICE_SEC = 0.25


class _KernelStopRequested(Exception):
    """Internal: stop_event became set during LLM stream or retry backoff."""


# ── Private types for _run_items() generator ─────────────


@dataclass
class _TerminalItem:
    """Kernel termination result, consumed only by run()."""

    status: str          # completed / cancelled / failed
    reason: str          # natural / max_turns / cancelled / hook_stopped / invalid_finish
    final_content: str | None = None
    num_turns: int = 0
    stop_reason: str | None = None
    usage: dict[str, int] = dc_field(default_factory=dict)


@dataclass
class _KernelItem:
    """Kernel private stream item. Not exposed publicly."""

    event: Any = None                       # BusEvent | None
    messages_delta: list[Any] | None = None  # list[Message] | None
    terminal: _TerminalItem | None = None


@dataclass
class _KernelState:
    """Kernel loop local state. Independent per _run_items() invocation."""

    messages: list[Any]  # list[Message]
    turn: int = 0
    total_usage: dict[str, int] = dc_field(default_factory=dict)
    last_stop_reason: str | None = None
    # Tool Runtime v2: catalog version caching
    last_catalog_version: int | None = None
    cached_tool_definitions: list[dict[str, Any]] | None = None


def _resolve_tool_definitions(
    spec: AgentRuntimeSpec,
    state: _KernelState,
) -> list[dict[str, Any]] | None:
    """Resolve tool definitions with Phase 1/Phase 2 dual-path.

    Phase 2 path: tool_catalog with version caching.
    Phase 1 fallback: direct registry access.
    """
    # Phase 2 path: tool_catalog with version caching
    if spec.tool_catalog is not None:
        current_version = spec.tool_catalog.version
        if current_version != state.last_catalog_version:
            state.cached_tool_definitions = spec.tool_catalog.build_definitions()
            state.last_catalog_version = current_version
        return state.cached_tool_definitions

    # Phase 1 fallback: direct registry access
    if (
        spec.tool_registry
        and hasattr(spec.tool_registry, 'get_tool_definitions')
    ):
        return spec.tool_registry.get_tool_definitions()
    return None


def _make_terminal(
    status: str,
    reason: str,
    state: _KernelState,
    final_content: str | None = None,
) -> _TerminalItem:
    """Build a _TerminalItem from current kernel state."""
    if reason == 'cancelled':
        resolved_status = 'cancelled'
    elif reason == 'invalid_finish':
        resolved_status = 'failed'
    else:
        resolved_status = status
    return _TerminalItem(
        status=resolved_status,
        reason=reason,
        final_content=final_content,
        num_turns=state.turn,
        stop_reason=state.last_stop_reason,
        usage=dict(state.total_usage),
    )


class AgentKernel:
    """Pure execution loop -- consumes AgentRuntimeSpec, no config assembly."""

    async def run(
        self,
        spec: AgentRuntimeSpec,
        task: str,
        history: list[Message] | None = None,
        stop_event: threading.Event | None = None,
    ) -> KernelRunResult:
        """Execute the agent loop until termination.

        Delegates to _run_items() and collects messages + terminal.

        Termination conditions:
        - natural: LLM returns no tool_calls
        - max_turns: turn counter reaches spec.max_turns
        - cancelled: stop_event is set
        - hook_stopped: should_continue hook returns False

        Args:
            spec: Runtime specification with tools, hooks, guards, LLM provider.
            task: The user's current task/prompt.
            history: Optional multi-turn conversation history to insert between
                     SystemMessage and UserMessage(task).
            stop_event: External cancellation signal.

        Returns KernelRunResult with event and message transcript.
        """
        async with spec.llm_provider:
            # Enter summary_provider if it's a separate instance
            _summary_provider = None
            if spec.compactor and hasattr(spec.compactor, '_summary_provider'):
                sp = spec.compactor._summary_provider
                if sp is not spec.llm_provider:
                    _summary_provider = sp

            async def _collect() -> KernelRunResult:
                all_messages: list[Message] = []
                async for item in self._run_items(spec, task, history, stop_event):
                    if item.messages_delta:
                        all_messages.extend(item.messages_delta)
                    if item.terminal is not None:
                        from matmaster.types.runtime import (
                            KernelResult,
                            KernelRunResult,
                        )
                        result = KernelResult(
                            status=item.terminal.status,
                            reason=item.terminal.reason,
                            final_content=item.terminal.final_content,
                            num_turns=item.terminal.num_turns,
                            stop_reason=item.terminal.stop_reason,
                            usage=item.terminal.usage,
                        )
                        return KernelRunResult(result=result, messages=all_messages)
                # Should not reach here, but defensive
                from matmaster.types.runtime import (
                    KernelResult,
                    KernelRunResult,
                )
                return KernelRunResult(
                    result=KernelResult(status="failed", reason="generator_exhausted"),
                    messages=all_messages,
                )

            if _summary_provider is not None:
                async with _summary_provider:
                    return await _collect()
            else:
                return await _collect()

    async def run_stream(
        self,
        spec: AgentRuntimeSpec,
        task: str,
        history: list[Message] | None = None,
        stop_event: threading.Event | None = None,
    ) -> AsyncIterator[Any]:
        """Yield BusEvent sequence from the kernel execution.

        Consumes _run_items() and filters events. Terminal item
        is converted to RunResultEvent as the final yield.
        """
        async with spec.llm_provider:
            _summary_provider = None
            if spec.compactor and hasattr(spec.compactor, '_summary_provider'):
                sp = spec.compactor._summary_provider
                if sp is not spec.llm_provider:
                    _summary_provider = sp

            async def _stream() -> AsyncIterator[Any]:
                async for item in self._run_items(spec, task, history, stop_event):
                    if item.event is not None:
                        yield item.event
                    if item.terminal is not None:
                        from matmaster.types.runtime import KernelResult
                        result = KernelResult(
                            status=item.terminal.status,
                            reason=item.terminal.reason,
                            final_content=item.terminal.final_content,
                            num_turns=item.terminal.num_turns,
                            stop_reason=item.terminal.stop_reason,
                            usage=item.terminal.usage,
                        )
                        yield result.to_run_result_event(source="agent")

            if _summary_provider is not None:
                async with _summary_provider:
                    async for event in _stream():
                        yield event
            else:
                async for event in _stream():
                    yield event

    async def _run_items(
        self,
        spec: AgentRuntimeSpec,
        task: str,
        history: list[Message] | None,
        stop_event: threading.Event | None,
    ) -> AsyncIterator[_KernelItem]:
        """Private AsyncGenerator: the single execution path.

        Yields _KernelItem instances carrying:
        - event: BusEvent snapshots (ThoughtEvent, ResponseEvent, etc.)
        - messages_delta: incremental message additions for run() collection
        - terminal: termination result (last yield, exactly once)
        """
        # Initialize local state (not on self -- keeps Kernel stateless)
        initial_messages: list[Message] = [
            SystemMessage(content=spec.system_prompt),
            *(history or []),
            UserMessage(content=task),
        ]
        state = _KernelState(messages=list(initial_messages))

        # Yield initial messages for run() to collect
        yield _KernelItem(messages_delta=list(initial_messages))

        guard_pipeline = GuardPipeline(spec.guards)

        # Resolve tool_runner: spec.tool_runner or fallback to InlineToolRunner
        tool_runner = spec.tool_runner
        if tool_runner is None:
            from matmaster.core.tool_runner import InlineToolRunner
            tool_runner = InlineToolRunner(spec, spec.guards)

        if spec.compactor:
            spec.compactor.update_message_count(len(state.messages))

        while state.turn < spec.max_turns:
            # External cancel check (before each turn)
            if stop_event and stop_event.is_set():
                yield _KernelItem(
                    terminal=_make_terminal('cancelled', 'cancelled', state)
                )
                return

            state.turn += 1

            # pre_llm_call hook (observation, all hooks called)
            await run_pre_llm_call(spec.hooks, state.messages, state.turn)

            # should_continue hook (intercepting, short-circuit)
            if not await run_should_continue(spec.hooks, state.messages, state.turn):
                # hook_stopped uses turn-1 because this turn didn't complete
                hook_state = _KernelState(
                    messages=state.messages,
                    turn=state.turn - 1,
                    total_usage=state.total_usage,
                    last_stop_reason=state.last_stop_reason,
                )
                yield _KernelItem(
                    terminal=_make_terminal('completed', 'hook_stopped', hook_state)
                )
                return

            # Context compaction check
            if spec.compactor:
                turn_usage = {}  # Will be set after LLM call
                await spec.compactor.compact_if_needed(
                    state.messages, turn_usage, state.turn
                )

            # Resolve tool definitions (Phase 1 registry or Phase 2 catalog)
            tool_defs = _resolve_tool_definitions(spec, state)

            # LLM call (streaming by default)
            try:
                response = await self._call_llm(
                    spec, state.messages, tool_defs=tool_defs, stop_event=stop_event
                )
            except _KernelStopRequested:
                yield _KernelItem(
                    terminal=_make_terminal('cancelled', 'cancelled', state)
                )
                return

            turn_usage = response.usage
            self._accumulate_usage(state.total_usage, response.usage)
            state.last_stop_reason = response.finish_reason
            if spec.compactor:
                spec.compactor.update_message_count(len(state.messages))

            # Natural finish: no tool_calls
            if not response.tool_calls:
                if not self._is_valid_natural_finish(response):
                    yield _KernelItem(
                        terminal=_make_terminal('failed', 'invalid_finish', state)
                    )
                    return

                assistant_msg = AssistantMessage(
                    content=response.content,
                    reasoning_content=response.reasoning_content,
                )
                state.messages.append(assistant_msg)
                yield _KernelItem(messages_delta=[assistant_msg])

                # Yield final completed snapshot events (KGEN-05)
                if response.reasoning_content:
                    yield _KernelItem(event=ThoughtEvent(
                        source="agent",
                        content=response.reasoning_content,
                        stream_state="complete",
                    ))
                if response.content:
                    yield _KernelItem(event=ResponseEvent(
                        source="agent",
                        content=response.content,
                        stream_state="complete",
                    ))

                yield _KernelItem(
                    terminal=_make_terminal(
                        'completed', 'natural', state,
                        final_content=response.content,
                    )
                )
                return

            # Has tool_calls: append assistant message then process tools
            assistant_msg = AssistantMessage(
                content=response.content,
                tool_calls=response.tool_calls,
                reasoning_content=response.reasoning_content,
            )
            state.messages.append(assistant_msg)
            yield _KernelItem(messages_delta=[assistant_msg])

            # KGEN-06: yield ToolCallEvent before execution
            for tc in response.tool_calls:
                yield _KernelItem(event=ToolCallEvent(
                    source="agent",
                    call_id=tc.id,
                    tool_name=tc.name,
                    arguments=tc.arguments,
                ))

            # Phase 1: delegate to tool_runner (InlineToolRunner wraps
            # guard -> pre_hook -> execute -> post_hook chain)
            from matmaster.core.tool_runner import ToolExecutionContext
            ctx = ToolExecutionContext(
                turn=state.turn,
                max_turns=spec.max_turns,
                stop_event=stop_event,
            )
            results = await tool_runner.execute_batch(
                response.tool_calls, ctx
            )

            # Collect tool messages in original order
            tool_messages: list[Message] = []
            for tc, tr in results:
                tool_messages.append(ToolMessage(
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    content=tr.content,
                ))
            state.messages.extend(tool_messages)
            yield _KernelItem(messages_delta=tool_messages)

            # KGEN-06: yield ToolResultEvent after execution
            for tc, tr in results:
                yield _KernelItem(event=ToolResultEvent(
                    source="agent",
                    call_id=tc.id,
                    tool_name=tc.name,
                    result=tr.content,
                    status=tr.status,
                    payload=tr.payload,
                ))

        # max_turns exhausted
        yield _KernelItem(
            terminal=_make_terminal('completed', 'max_turns', state)
        )

    async def _call_llm(
        self,
        spec: AgentRuntimeSpec,
        messages: list[Message],
        *,
        tool_defs: list[dict[str, Any]] | None = None,
        stop_event: threading.Event | None = None,
    ) -> LLMResponse:
        """Call LLM with timeout-doubling retry on transient errors."""
        provider = spec.llm_provider
        current_timeout = getattr(provider, 'stream_timeout', None) or getattr(
            provider, '_timeout', 300.0
        )
        max_retries = getattr(provider, 'max_retries', 3)
        retry_delay = getattr(provider, 'retry_delay', 1.0)

        # Serialize once -- messages don't change between retries
        api_messages = [m.to_api_dict() for m in messages]
        # Use provided tool_defs or fall back to legacy inline resolution
        if tool_defs is None:
            tool_defs = (
                spec.tool_registry.get_tool_definitions()
                if spec.tool_registry
                and hasattr(spec.tool_registry, 'get_tool_definitions')
                else None
            )

        attempt_records: list[dict[str, Any]] | None = None
        last_error: LLMError | None = None
        for attempt in range(max_retries):
            if stop_event and stop_event.is_set():
                raise _KernelStopRequested()
            t0 = time.monotonic()
            try:
                response = await self._do_stream_llm(
                    spec,
                    api_messages,
                    tool_defs,
                    timeout=current_timeout,
                    stop_event=stop_event,
                )
                elapsed = time.monotonic() - t0

                if self._is_incomplete_response(response) and attempt < max_retries - 1:
                    backoff = retry_delay * (2**attempt)
                    if attempt_records is None:
                        attempt_records = []
                    attempt_records.append(
                        {
                            "attempt": attempt + 1,
                            "error_type": "IncompleteResponse",
                            "error_category": "incomplete_response",
                            "error_message": "reasoning-only response without content",
                            "timeout_used": current_timeout,
                            "elapsed_seconds": round(elapsed, 2),
                            "retryable": True,
                            "backoff_seconds": backoff,
                        }
                    )
                    logger.warning(
                        "LLM returned reasoning without content "
                        "(attempt %d/%d, elapsed=%.1fs), retrying.",
                        attempt + 1,
                        max_retries,
                        elapsed,
                    )
                    await self._sleep_backoff_with_stop_async(backoff, stop_event)
                    continue

                # Last attempt still incomplete -- return degraded
                if self._is_incomplete_response(response):
                    logger.warning(
                        "LLM returned incomplete response after %d attempts, "
                        "returning degraded result.",
                        max_retries,
                    )
                    response.degraded = True
                return response
            except LLMError as e:
                elapsed = time.monotonic() - t0
                if not e.retryable:
                    raise
                last_error = e
                next_timeout = current_timeout * 2
                backoff = (
                    retry_delay * (2**attempt) if attempt < max_retries - 1 else 0.0
                )
                if attempt_records is None:
                    attempt_records = []
                attempt_records.append(
                    {
                        "attempt": attempt + 1,
                        "error_type": (
                            type(e.__cause__).__name__
                            if e.__cause__
                            else type(e).__name__
                        ),
                        "error_category": getattr(e, "error_category", None),
                        "error_message": str(e),
                        "timeout_used": current_timeout,
                        "elapsed_seconds": round(elapsed, 2),
                        "retryable": e.retryable,
                        "next_timeout": next_timeout,
                        "backoff_seconds": backoff,
                    }
                )
                logger.warning(
                    "LLM call failed (attempt %d/%d) [%s]: %s "
                    "(timeout=%.0fs, elapsed=%.1fs, backoff=%.1fs, next_timeout=%.0fs)",
                    attempt + 1,
                    max_retries,
                    getattr(e, "error_category", None) or "unknown",
                    e,
                    current_timeout,
                    elapsed,
                    backoff,
                    next_timeout,
                )
                current_timeout = next_timeout
                if attempt < max_retries - 1:
                    await self._sleep_backoff_with_stop_async(backoff, stop_event)

        # Retries exhausted
        if last_error is not None:
            msg = (
                f"LLM stream failed after {max_retries} attempts: "
                f"last error [{last_error.error_category or 'unknown'}] {last_error}"
            )
            category = last_error.error_category
        else:
            msg = (
                f"LLM stream failed after {max_retries} attempts: "
                f"all attempts returned incomplete responses"
            )
            category = "incomplete_response"

        raise LLMError(
            msg,
            retryable=False,
            error_category=category,
            attempts=attempt_records or [],
        ) from last_error

    @staticmethod
    async def _sleep_backoff_with_stop_async(
        seconds: float,
        stop_event: threading.Event | None,
    ) -> None:
        """Async sleep for *seconds*, but wake early if stop_event is set."""
        if seconds <= 0:
            return
        if not stop_event:
            await asyncio.sleep(seconds)
            return
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if stop_event.is_set():
                raise _KernelStopRequested()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(_STOP_RETRY_SLEEP_SLICE_SEC, remaining))

    async def _do_stream_llm(
        self,
        spec: AgentRuntimeSpec,
        api_messages: list[dict[str, Any]],
        tool_defs: list[dict[str, Any]] | None,
        *,
        timeout: float | None = None,
        stop_event: threading.Event | None = None,
    ) -> LLMResponse:
        """Call LLM via streaming, accumulate chunks into LLMResponse."""

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls_acc: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        stream_id = f'turn-{len(api_messages)}'
        usage: dict[str, int] = {}
        producing_reasoning = False
        producing_content = False

        await run_on_stream_chunk(
            spec.hooks,
            StreamChunk(stream_state='start', stream_id=stream_id),
        )
        stream_cancelled = False
        chunk_idx = 0
        t_stream0 = time.perf_counter()
        ttft_ms: float | None = None
        try:
            async for chunk in spec.llm_provider.chat_stream(
                api_messages, tool_defs, timeout=timeout
            ):
                if (
                    stop_event
                    and chunk_idx % _STOP_CHECK_EVERY_N_STREAM_CHUNKS == 0
                    and stop_event.is_set()
                ):
                    stream_cancelled = True
                    break
                chunk_idx += 1
                if ttft_ms is None and (
                    chunk.content or chunk.reasoning_content or chunk.tool_call_deltas
                ):
                    ttft_ms = (time.perf_counter() - t_stream0) * 1000.0
                if chunk.content or chunk.reasoning_content:
                    await run_on_stream_chunk(
                        spec.hooks,
                        chunk.model_copy(
                            update={
                                'stream_state': 'streaming',
                                'stream_id': stream_id,
                            }
                        ),
                    )

                if chunk.reasoning_content:
                    reasoning_parts.append(chunk.reasoning_content)
                    producing_reasoning = True

                if chunk.content:
                    if producing_reasoning:
                        await run_on_segment_complete(
                            spec.hooks,
                            'thought',
                            ''.join(reasoning_parts),
                            stream_id,
                        )
                        producing_reasoning = False
                    content_parts.append(chunk.content)
                    producing_content = True

                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
                if chunk.usage:
                    usage = chunk.usage
                if chunk.tool_call_deltas:
                    if producing_reasoning:
                        await run_on_segment_complete(
                            spec.hooks,
                            'thought',
                            ''.join(reasoning_parts),
                            stream_id,
                        )
                        producing_reasoning = False
                    if producing_content:
                        await run_on_segment_complete(
                            spec.hooks,
                            'response',
                            ''.join(content_parts),
                            stream_id,
                        )
                        producing_content = False
                    for delta in chunk.tool_call_deltas:
                        idx = delta.get('index', 0)
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                'id': '',
                                'name': '',
                                'arguments': '',
                            }
                        if delta.get('id'):
                            tool_calls_acc[idx]['id'] = delta['id']
                        if delta.get('name'):
                            tool_calls_acc[idx]['name'] = delta['name']
                        if delta.get('arguments'):
                            tool_calls_acc[idx]['arguments'] += delta['arguments']
        finally:
            if producing_reasoning:
                await run_on_segment_complete(
                    spec.hooks,
                    'thought',
                    ''.join(reasoning_parts),
                    stream_id,
                )
            if producing_content:
                await run_on_segment_complete(
                    spec.hooks,
                    'response',
                    ''.join(content_parts),
                    stream_id,
                )
            await run_on_stream_chunk(
                spec.hooks,
                StreamChunk(stream_state='end', stream_id=stream_id),
            )

        if stream_cancelled:
            raise _KernelStopRequested()

        total_stream_ms = (time.perf_counter() - t_stream0) * 1000.0
        joined_content = ''.join(content_parts)
        joined_reasoning = ''.join(reasoning_parts)
        logger.info(
            'LLM stream timing: stream_id=%s api_messages=%d chunks=%d '
            'ttft_ms=%s total_ms=%.1f content_chars=%d reasoning_chars=%d has_tool_calls=%s',
            stream_id,
            len(api_messages),
            chunk_idx,
            f'{ttft_ms:.1f}' if ttft_ms is not None else 'n/a',
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
                args = parse_tool_arguments(v['arguments'])
                tool_calls.append(
                    ToolCallData(id=v['id'], name=v['name'], arguments=args)
                )

        return LLMResponse(
            content=joined_content or None,
            reasoning_content=joined_reasoning or None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    @staticmethod
    def _is_valid_natural_finish(response: LLMResponse) -> bool:
        """Only commit a natural finish when the stream terminates cleanly."""
        return not response.tool_calls and response.finish_reason == 'stop'

    @staticmethod
    def _is_incomplete_response(response: LLMResponse) -> bool:
        """Detect reasoning-only response with no visible content.

        This can happen when an LLM proxy (e.g. LiteLLM) intermittently
        drops the content block after streaming the thinking block.
        """
        return (
            response.content is None
            and response.reasoning_content is not None
            and not response.tool_calls
        )

    @staticmethod
    def _accumulate_usage(total: dict[str, int], delta: dict[str, int]) -> None:
        """Accumulate per-turn usage into running total."""
        for k, v in delta.items():
            total[k] = total.get(k, 0) + v
