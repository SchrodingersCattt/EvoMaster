"""AgentKernel -- pure async execution loop for the agent kernel.

Consumes an AgentRuntimeSpec and executes the LLM -> guard -> hook -> tool
-> message accumulate -> loop cycle. All termination paths go through
_finish() which produces a KernelResult.

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
from typing import TYPE_CHECKING, Any, NamedTuple

from matmaster.core.guard_pipeline import GuardPipeline
from matmaster.tools.tool_result import ToolResult
from matmaster.types.errors import LLMError

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


class _ToolOutcome(NamedTuple):
    """Result of guard + pre-hook gating for a single tool call."""

    tc: ToolCallData
    tool_msg: ToolMessage | None
    tool_result: ToolResult | None
    needs_post_hook: bool


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

            if _summary_provider is not None:
                async with _summary_provider:
                    return await self._run_loop(spec, task, history, stop_event)
            else:
                return await self._run_loop(spec, task, history, stop_event)

    async def _run_loop(
        self,
        spec: AgentRuntimeSpec,
        task: str,
        history: list[Message] | None,
        stop_event: threading.Event | None,
    ) -> KernelRunResult:
        """Internal async execution loop."""
        messages: list[Message] = [
            SystemMessage(content=spec.system_prompt),
            *(history or []),
            UserMessage(content=task),
        ]
        guard_pipeline = GuardPipeline(spec.guards, read_tracker=spec.read_tracker)

        turn = 0
        if spec.compactor:
            spec.compactor.update_message_count(len(messages))
        turn_usage: dict[str, int] = {}
        total_usage: dict[str, int] = {}
        last_stop_reason: str | None = None

        while turn < spec.max_turns:
            # External cancel check (before each turn)
            if stop_event and stop_event.is_set():
                return self._finish(
                    messages,
                    'cancelled',
                    num_turns=turn,
                    stop_reason=last_stop_reason,
                    usage=total_usage,
                )

            turn += 1

            # pre_llm_call hook (observation, all hooks called)
            await run_pre_llm_call(spec.hooks, messages, turn)

            # should_continue hook (intercepting, short-circuit)
            if not await run_should_continue(spec.hooks, messages, turn):
                return self._finish(
                    messages,
                    'hook_stopped',
                    num_turns=turn - 1,
                    stop_reason=last_stop_reason,
                    usage=total_usage,
                )

            # Context compaction check
            if spec.compactor:
                await spec.compactor.compact_if_needed(messages, turn_usage, turn)

            # LLM call (streaming by default)
            try:
                response = await self._call_llm(spec, messages, stop_event=stop_event)
            except _KernelStopRequested:
                return self._finish(
                    messages,
                    'cancelled',
                    num_turns=turn,
                    stop_reason=last_stop_reason,
                    usage=total_usage,
                )
            turn_usage = response.usage
            self._accumulate_usage(total_usage, response.usage)
            last_stop_reason = response.finish_reason
            if spec.compactor:
                spec.compactor.update_message_count(len(messages))

            # Natural finish: no tool_calls
            if not response.tool_calls:
                if not self._is_valid_natural_finish(response):
                    return self._finish(
                        messages,
                        'invalid_finish',
                        num_turns=turn,
                        stop_reason=last_stop_reason,
                        usage=total_usage,
                    )
                messages.append(
                    AssistantMessage(
                        content=response.content,
                        reasoning_content=response.reasoning_content,
                    )
                )
                return self._finish(
                    messages,
                    'natural',
                    final_content=response.content,
                    num_turns=turn,
                    stop_reason=response.finish_reason,
                    usage=total_usage,
                )

            # Has tool_calls: append assistant message then process each serially
            messages.append(
                AssistantMessage(
                    content=response.content,
                    tool_calls=response.tool_calls,
                    reasoning_content=response.reasoning_content,
                )
            )

            # Phase 1: Serial — guard denials and hook skips must resolve before execution to avoid wasted work
            outcomes: list[_ToolOutcome] = []
            approved_indices: list[int] = []

            for _i, tc in enumerate(response.tool_calls):
                if stop_event and stop_event.is_set():
                    return self._finish(
                        messages,
                        'cancelled',
                        num_turns=turn,
                        stop_reason=last_stop_reason,
                        usage=total_usage,
                    )
                guard_result = guard_pipeline.evaluate(tc, turn, spec.max_turns)
                if not guard_result.allowed:
                    await run_guard_blocked(spec.hooks, tc, guard_result)
                    blocked_content = f'BLOCKED: {guard_result.reason}'
                    if guard_result.guidance:
                        blocked_content += f'\n{guard_result.guidance}'
                    outcomes.append(
                        _ToolOutcome(
                            tc=tc,
                            tool_msg=ToolMessage(
                                tool_call_id=tc.id,
                                tool_name=tc.name,
                                content=blocked_content,
                            ),
                            tool_result=None,
                            needs_post_hook=False,
                        )
                    )
                    continue

                action = await run_pre_tool_call(spec.hooks, tc)
                if action == HookAction.SKIP:
                    outcomes.append(
                        _ToolOutcome(
                            tc=tc,
                            tool_msg=ToolMessage(
                                tool_call_id=tc.id,
                                tool_name=tc.name,
                                content='Tool call skipped by hook.',
                            ),
                            tool_result=None,
                            needs_post_hook=False,
                        )
                    )
                    continue

                approved_indices.append(len(outcomes))
                outcomes.append(
                    _ToolOutcome(
                        tc=tc, tool_msg=None, tool_result=None, needs_post_hook=True
                    )
                )

            # Phase 2: Parallel — approved tools are independent, concurrent execution reduces latency
            if approved_indices:
                approved_tcs = [outcomes[idx][0] for idx in approved_indices]

                async def _execute_tool(tc: ToolCallData) -> ToolResult:
                    try:
                        return await spec.tool_registry.execute(tc.name, tc.arguments)
                    except Exception as e:
                        logger.exception('Tool execution failed: %s', tc.name)
                        return ToolResult.from_error(tc.name, e)

                results = await asyncio.gather(
                    *[_execute_tool(tc) for tc in approved_tcs],
                    return_exceptions=True,
                )

                for result_idx, outcome_idx in enumerate(approved_indices):
                    tc = outcomes[outcome_idx].tc
                    raw = results[result_idx]
                    if isinstance(raw, BaseException):
                        tool_result = ToolResult.from_error(tc.name, raw)
                    else:
                        tool_result = raw
                    outcomes[outcome_idx] = _ToolOutcome(
                        tc=tc,
                        tool_msg=ToolMessage(
                            tool_call_id=tc.id,
                            tool_name=tc.name,
                            content=tool_result.content,
                        ),
                        tool_result=tool_result,
                        needs_post_hook=True,
                    )

            # Phase 3: Append in original order — LLM expects tool results to match request order
            for tc, tool_msg, tool_result, needs_post_hook in outcomes:
                messages.append(tool_msg)
                if needs_post_hook and tool_result is not None:
                    await run_post_tool_call(spec.hooks, tc, tool_result)

        # max_turns exhausted
        return self._finish(
            messages,
            'max_turns',
            num_turns=turn,
            stop_reason=last_stop_reason,
            usage=total_usage,
        )

    async def _call_llm(
        self,
        spec: AgentRuntimeSpec,
        messages: list[Message],
        *,
        stop_event: threading.Event | None = None,
    ) -> LLMResponse:
        """Call LLM with timeout-doubling retry on transient errors."""
        provider = spec.llm_provider
        current_timeout = getattr(provider, 'stream_timeout', None) or getattr(
            provider, '_timeout', 300.0
        )
        max_retries = getattr(provider, 'max_retries', 3)
        retry_delay = getattr(provider, 'retry_delay', 1.0)

        # Serialize once — messages don't change between retries
        api_messages = [m.to_api_dict() for m in messages]
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

                # Last attempt still incomplete — return degraded
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

    @staticmethod
    def _finish(
        messages: list[Message],
        reason: str,
        final_content: str | None = None,
        *,
        num_turns: int = 0,
        stop_reason: str | None = None,
        usage: dict[str, int] | None = None,
    ) -> KernelRunResult:
        """Unified exit path -- all termination goes through here."""
        if reason == 'cancelled':
            status = 'cancelled'
        elif reason == 'invalid_finish':
            status = 'failed'
        else:
            status = 'completed'
        from matmaster.types.runtime import (  # lazy to avoid circular
            KernelResult,
            KernelRunResult,
        )

        result = KernelResult(
            status=status,
            reason=reason,
            final_content=final_content,
            num_turns=num_turns,
            stop_reason=stop_reason,
            usage=dict(usage) if usage else {},
        )
        return KernelRunResult(result=result, messages=list(messages))
