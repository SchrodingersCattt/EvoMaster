"""AgentKernel -- pure execution loop for the agent kernel.

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

import json
import logging
import threading
import time
from typing import TYPE_CHECKING, Any

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
)

logger = logging.getLogger(__name__)

# 流式输出中每隔 N 个 chunk 检查一次 stop_event（避免每 chunk 打 Redis EXISTS）
_STOP_CHECK_EVERY_N_STREAM_CHUNKS = 8
# 重试退避时切片 sleep 的步长（秒），便于尽快响应停止
_STOP_RETRY_SLEEP_SLICE_SEC = 0.25


class _KernelStopRequested(Exception):
    """Internal: stop_event became set during LLM stream or retry backoff."""


class AgentKernel:
    """Pure execution loop -- consumes AgentRuntimeSpec, no config assembly."""

    def run(
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
        messages: list[Message] = [
            SystemMessage(content=spec.system_prompt),
            *(history or []),
            UserMessage(content=task),
        ]
        guard_pipeline = GuardPipeline(spec.guards)
        turn = 0
        if spec.compactor:
            spec.compactor.update_message_count(len(messages))
        last_usage: dict[str, int] = {}
        total_usage: dict[str, int] = {}
        last_stop_reason: str | None = None

        while turn < spec.max_turns:
            # External cancel check (before each turn)
            if stop_event and stop_event.is_set():
                return self._finish(
                    spec,
                    messages,
                    'cancelled',
                    num_turns=turn,
                    stop_reason=last_stop_reason,
                    usage=total_usage,
                )

            turn += 1

            # pre_llm_call hook (observation, all hooks called)
            run_pre_llm_call(spec.hooks, messages, turn)

            # should_continue hook (intercepting, short-circuit)
            if not run_should_continue(spec.hooks, messages, turn):
                return self._finish(
                    spec,
                    messages,
                    'hook_stopped',
                    num_turns=turn - 1,
                    stop_reason=last_stop_reason,
                    usage=total_usage,
                )

            # Context compaction check
            if spec.compactor:
                spec.compactor.compact_if_needed(messages, last_usage, turn)

            # LLM call (streaming by default)
            try:
                response = self._call_llm(spec, messages, stop_event=stop_event)
            except _KernelStopRequested:
                return self._finish(
                    spec,
                    messages,
                    'cancelled',
                    num_turns=turn,
                    stop_reason=last_stop_reason,
                    usage=total_usage,
                )
            last_usage = response.usage
            self._accumulate_usage(total_usage, response.usage)
            last_stop_reason = response.finish_reason
            if spec.compactor:
                spec.compactor.update_message_count(len(messages))

            # Natural finish: no tool_calls
            if not response.tool_calls:
                if not self._is_valid_natural_finish(response):
                    return self._finish(
                        spec,
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
                    spec,
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

            for tc in response.tool_calls:
                if stop_event and stop_event.is_set():
                    return self._finish(
                        spec,
                        messages,
                        'cancelled',
                        num_turns=turn,
                        stop_reason=last_stop_reason,
                        usage=total_usage,
                    )
                # Guard evaluation (before hooks)
                guard_result = guard_pipeline.evaluate(tc, turn, spec.max_turns)
                if not guard_result.allowed:
                    # Blocked: notify hooks, then append ToolMessage error
                    run_guard_blocked(spec.hooks, tc, guard_result)
                    blocked_content = f'BLOCKED: {guard_result.reason}'
                    if guard_result.guidance:
                        blocked_content += f'\n{guard_result.guidance}'
                    messages.append(
                        ToolMessage(
                            tool_call_id=tc.id,
                            tool_name=tc.name,
                            content=blocked_content,
                        )
                    )
                    continue

                # pre_tool_call hook (intercepting, short-circuit)
                action = run_pre_tool_call(spec.hooks, tc)
                if action == HookAction.SKIP:
                    messages.append(
                        ToolMessage(
                            tool_call_id=tc.id,
                            tool_name=tc.name,
                            content='Tool call skipped by hook.',
                        )
                    )
                    continue

                # Tool execution
                try:
                    tool_result = spec.tool_registry.execute(tc.name, tc.arguments)
                except Exception as e:
                    tool_result = ToolResult(
                        status='error',
                        content=(
                            f"Error executing tool '{tc.name}': "
                            f'{type(e).__name__}: {e}'
                        ),
                    )
                    logger.exception('Tool execution failed: %s', tc.name)
                messages.append(
                    ToolMessage(
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                        content=tool_result.content,
                    )
                )

                # post_tool_call hook (observation, all hooks called)
                run_post_tool_call(spec.hooks, tc, tool_result)

        # max_turns exhausted
        return self._finish(
            spec,
            messages,
            'max_turns',
            num_turns=turn,
            stop_reason=last_stop_reason,
            usage=total_usage,
        )

    def _call_llm(
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

        last_error: LLMError | None = None
        for attempt in range(max_retries):
            if stop_event and stop_event.is_set():
                raise _KernelStopRequested()
            try:
                return self._do_stream_llm(
                    spec, messages, timeout=current_timeout, stop_event=stop_event
                )
            except LLMError as e:
                if not e.retryable:
                    raise
                last_error = e
                next_timeout = current_timeout * 2
                logger.warning(
                    'LLM stream timed out after %.0fs (attempt %d/%d). '
                    'Retrying with timeout=%.0fs.',
                    current_timeout,
                    attempt + 1,
                    max_retries,
                    next_timeout,
                )
                current_timeout = next_timeout
                if attempt < max_retries - 1:
                    backoff = retry_delay * (2**attempt)
                    self._sleep_backoff_with_stop(backoff, stop_event)

        raise RuntimeError(
            f'LLM stream failed after {max_retries} attempts'
        ) from last_error

    @staticmethod
    def _sleep_backoff_with_stop(
        seconds: float, stop_event: threading.Event | None
    ) -> None:
        """Sleep for `seconds`, but wake early if stop_event is set."""
        if seconds <= 0:
            return
        if not stop_event:
            time.sleep(seconds)
            return
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if stop_event.is_set():
                raise _KernelStopRequested()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(_STOP_RETRY_SLEEP_SLICE_SEC, remaining))

    def _do_stream_llm(
        self,
        spec: AgentRuntimeSpec,
        messages: list[Message],
        *,
        timeout: float | None = None,
        stop_event: threading.Event | None = None,
    ) -> LLMResponse:
        """Call LLM via streaming, accumulate chunks into LLMResponse."""
        api_messages = [m.to_api_dict() for m in messages]
        tool_defs = (
            spec.tool_registry.get_tool_definitions()
            if spec.tool_registry
            and hasattr(spec.tool_registry, 'get_tool_definitions')
            else None
        )

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls_acc: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        stream_id = f'turn-{len(messages)}'
        usage: dict[str, int] = {}
        producing_reasoning = False
        producing_content = False

        run_on_stream_chunk(
            spec.hooks,
            StreamChunk(stream_state='start', stream_id=stream_id),
        )
        stream_cancelled = False
        chunk_idx = 0
        try:
            for chunk in spec.llm_provider.chat_stream(
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
                if chunk.content or chunk.reasoning_content:
                    run_on_stream_chunk(
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
                        run_on_segment_complete(
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
                        run_on_segment_complete(
                            spec.hooks,
                            'thought',
                            ''.join(reasoning_parts),
                            stream_id,
                        )
                        producing_reasoning = False
                    if producing_content:
                        run_on_segment_complete(
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
                            tool_calls_acc[idx]['name'] += delta['name']
                        if delta.get('arguments'):
                            tool_calls_acc[idx]['arguments'] += delta['arguments']
        finally:
            if producing_reasoning:
                run_on_segment_complete(
                    spec.hooks,
                    'thought',
                    ''.join(reasoning_parts),
                    stream_id,
                )
            if producing_content:
                run_on_segment_complete(
                    spec.hooks,
                    'response',
                    ''.join(content_parts),
                    stream_id,
                )
            run_on_stream_chunk(
                spec.hooks,
                StreamChunk(stream_state='end', stream_id=stream_id),
            )

        if stream_cancelled:
            raise _KernelStopRequested()

        # Assemble tool_calls from accumulated deltas
        tool_calls: list[ToolCallData] | None = None
        if tool_calls_acc:
            tool_calls = []
            for _, v in sorted(tool_calls_acc.items()):
                args = self._parse_arguments(v['arguments'])
                tool_calls.append(
                    ToolCallData(id=v['id'], name=v['name'], arguments=args)
                )

        return LLMResponse(
            content=''.join(content_parts) or None,
            reasoning_content=''.join(reasoning_parts) or None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    @staticmethod
    def _parse_arguments(raw: str) -> dict[str, Any]:
        """Parse JSON arguments string from streaming accumulation."""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.warning('Failed to parse tool call arguments: %s', raw[:200])
            return {'_raw': raw}

    @staticmethod
    def _is_valid_natural_finish(response: LLMResponse) -> bool:
        """Only commit a natural finish when the stream terminates cleanly."""
        return not response.tool_calls and response.finish_reason == 'stop'

    @staticmethod
    def _accumulate_usage(total: dict[str, int], delta: dict[str, int]) -> None:
        """Accumulate per-turn usage into running total.

        After summing raw counters, derives cache-adjusted keys so that
        downstream budget checks can compare apples-to-apples with
        Claude Code (which excludes cache-hit tokens from its totals).
        """
        for k, v in delta.items():
            total[k] = total.get(k, 0) + v
        # Derive cache-adjusted totals when cache info is available
        cache_read = total.get("cache_read_tokens", 0)
        if cache_read:
            total["prompt_tokens_uncached"] = total.get("prompt_tokens", 0) - cache_read
            total["total_tokens_uncached"] = total[
                "prompt_tokens_uncached"
            ] + total.get("completion_tokens", 0)

    @staticmethod
    def _finish(
        spec: AgentRuntimeSpec,
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
