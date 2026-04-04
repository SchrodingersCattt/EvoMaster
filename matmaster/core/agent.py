"""AgentKernel -- pure async execution loop for the agent kernel.

Consumes an AgentRuntimeSpec and executes the LLM -> hook -> tool
-> message accumulate -> loop cycle via run_stream(), the sole public API.
run_stream() yields BusEvent objects through the _run_items() generator.

Termination conditions:
- natural: LLM returns no tool_calls
- max_turns: turn counter reaches spec.max_turns
- cancelled: cancel_token is set (checked each turn, during stream chunks, retry
  backoff, and between serial tool_calls)
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field as dc_field
from typing import TYPE_CHECKING, Any

from matmaster.types.errors import LLMError
from matmaster.types.cancellation import CancellationToken
from matmaster.types.events import (
    AssistantStateEvent,
    ResponseEvent,
    SkillHitEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
)

if TYPE_CHECKING:
    from matmaster.types.runtime import AgentRuntimeSpec

from matmaster.core.hooks import (
    CompactionContext,
    HookEvent,
    RunContext,
    UserPromptContext,
)
from matmaster.types.messages import (
    AssistantMessage,
    LLMResponse,
    Message,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
    parse_tool_arguments,
)

logger = logging.getLogger(__name__)

# 流式输出中每隔 N 个 chunk 检查一次 cancel_token（避免每 chunk 打 Redis EXISTS）
_STOP_CHECK_EVERY_N_STREAM_CHUNKS = 8
# 重试退避时切片 sleep 的步长（秒），便于尽快响应停止
_STOP_RETRY_SLEEP_SLICE_SEC = 0.25


@dataclass
class _TerminalItem:
    """Signals that the kernel loop reached a terminal state."""

    reason: str
    final_content: str | None = None
    num_turns: int = 0
    usage: dict[str, int] = dc_field(default_factory=dict)
    messages: list[Any] = dc_field(default_factory=list)


@dataclass
class _KernelItem:
    """Single yield from _run_items() / _stream_llm_items().

    Exactly one of event, llm_response, messages_delta, or terminal is set.
    """

    event: Any = None  # BusEvent | None
    llm_response: LLMResponse | None = None
    messages_delta: list[Any] | None = None
    terminal: _TerminalItem | None = None


@dataclass
class _KernelState:
    """Mutable state for _run_items(). Preserves Kernel statelessness."""

    messages: list[Any]
    turn: int = 0
    total_usage: dict[str, int] = dc_field(default_factory=dict)
    cached_tool_definitions: list[dict[str, Any]] | None = None
    last_catalog_version: int = -1


class _KernelStopRequested(Exception):
    """Internal: cancel_token became set during LLM stream or retry backoff."""


class AgentKernel:
    """Pure execution loop -- consumes AgentRuntimeSpec, no config assembly."""

    async def run_stream(
        self,
        spec: AgentRuntimeSpec,
        task: str,
        history: list[Message] | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> AsyncIterator[Any]:
        """Generator-first entry point: yields BusEvent objects.

        Consumes _KernelItem from _run_items(), extracts .event for
        non-terminal items, and converts terminal items to RunResultEvent.
        Items with event=None (llm_response, messages_delta) are consumed
        internally and not yielded.
        """
        from matmaster.types.events import RunResultEvent

        async with spec.llm_provider:
            _summary_provider = None
            if spec.compactor and hasattr(spec.compactor, '_summary_provider'):
                sp = spec.compactor._summary_provider
                if sp is not spec.llm_provider:
                    _summary_provider = sp
            last_reason: str | None = None

            async def _consume_and_yield():
                nonlocal last_reason
                async for item in self._run_items(spec, task, history, cancel_token):
                    if item.terminal is not None:
                        reason = item.terminal.reason
                        last_reason = reason
                        status = 'cancelled' if reason == 'cancelled' else (
                            'failed' if reason == 'invalid_finish' else 'completed'
                        )
                        yield RunResultEvent(
                            source="agent",
                            status=status,
                            reason=reason,
                            final_content=item.terminal.final_content,
                            num_turns=item.terminal.num_turns,
                            usage=item.terminal.usage,
                            messages=item.terminal.messages,
                        )
                        return
                    if item.event is not None:
                        yield item.event

            if spec.hook_executor is not None:
                await spec.hook_executor.emit(
                    HookEvent.RUN_START,
                    RunContext(
                        task_id=spec.meta.get("task_id", ""),
                        session_id=spec.meta.get("session_id", ""),
                        reason="startup",
                    ),
                )

            try:
                if _summary_provider is not None:
                    async with _summary_provider:
                        async for event in _consume_and_yield():
                            yield event
                else:
                    async for event in _consume_and_yield():
                        yield event
            except BaseException as exc:
                if last_reason is None:
                    if isinstance(exc, (GeneratorExit, asyncio.CancelledError)):
                        last_reason = 'cancelled'
                    else:
                        last_reason = 'error'
                raise
            finally:
                if spec.hook_executor is not None:
                    await spec.hook_executor.emit(
                        HookEvent.RUN_END,
                        RunContext(
                            task_id=spec.meta.get("task_id", ""),
                            session_id=spec.meta.get("session_id", ""),
                            reason=last_reason or 'error',
                        ),
                    )

    @staticmethod
    def _terminal(
        state: _KernelState,
        reason: str,
        *,
        final_content: str | None = None,
        turn_offset: int = 0,
    ) -> _KernelItem:
        return _KernelItem(
            terminal=_TerminalItem(
                reason=reason,
                final_content=final_content,
                num_turns=state.turn + turn_offset,
                usage=dict(state.total_usage),
                messages=list(state.messages),
            )
        )

    async def _run_items(
        self,
        spec: AgentRuntimeSpec,
        task: str,
        history: list[Message] | None,
        cancel_token: CancellationToken | None,
    ) -> AsyncIterator[_KernelItem]:
        """Core generator loop: yields _KernelItem for each event.

        Yields events for streaming, AssistantState, and SkillHit.
        """
        from matmaster.core.tool_runner import ToolExecutionContext

        if spec.hook_executor is not None:
            session_id = spec.meta.get("session_id", "")
            prompt_ctx = UserPromptContext(prompt=task, session_id=session_id)
            task = await spec.hook_executor.emit_rewrite(
                HookEvent.USER_PROMPT_SUBMIT,
                prompt_ctx,
                task,
            )
            await spec.hook_executor.emit(
                HookEvent.USER_PROMPT_SUBMIT,
                UserPromptContext(prompt=task, session_id=session_id),
            )

        state = _KernelState(
            messages=[
                SystemMessage(content=spec.system_prompt),
                *(history or []),
                UserMessage(content=task),
            ]
        )

        compactor_events: deque[tuple[Any, int, int]] = deque()
        compaction_prev_count = len(state.messages)

        async def _compactor_sink(event: Any) -> None:
            nonlocal compaction_prev_count
            messages_after = len(state.messages)
            compactor_events.append((event, compaction_prev_count, messages_after))
            compaction_prev_count = messages_after

        if spec.compactor:
            spec.compactor._event_sink = _compactor_sink
            spec.compactor.update_message_count(len(state.messages))

        turn_usage: dict[str, int] = {}

        while state.turn < spec.max_turns:
            if cancel_token and cancel_token.is_cancelled:
                yield self._terminal(state, 'cancelled')
                return

            state.turn += 1

            if spec.compactor:
                compaction_prev_count = len(state.messages)
                await spec.compactor.compact_if_needed(
                    state.messages, turn_usage, state.turn
                )
                while compactor_events:
                    (
                        compaction_event,
                        messages_before,
                        messages_after,
                    ) = compactor_events.popleft()
                    yield _KernelItem(event=compaction_event)
                    if spec.hook_executor is not None and hasattr(
                        compaction_event, "payload"
                    ):
                        payload = getattr(compaction_event, "payload", {}) or {}
                        await spec.hook_executor.emit(
                            HookEvent.CONTEXT_COMPACTION,
                            CompactionContext(
                                messages_before=messages_before,
                                messages_after=messages_after,
                                trigger_tokens=payload.get("trigger_tokens", 0),
                                strategy=payload.get("strategy", "unknown"),
                            ),
                        )

            # ── Tool definitions resolution (version-aware caching) ──
            if (
                spec.tool_catalog is not None
                and hasattr(spec.tool_catalog, 'version')
                and spec.tool_catalog.version != state.last_catalog_version
            ):
                state.cached_tool_definitions = None
                state.last_catalog_version = spec.tool_catalog.version

            if state.cached_tool_definitions is None:
                if (
                    spec.tool_catalog is not None
                    and hasattr(spec.tool_catalog, 'build_definitions')
                ):
                    from matmaster.types.tool_desc_ctx import ToolDescriptionContext

                    desc_ctx = None
                    if spec.runtime_topology is not None:
                        desc_ctx = ToolDescriptionContext(
                            session_kind=spec.runtime_topology.session_kind,
                            workspace_root=spec.runtime_topology.workspace_root,
                            topology=spec.runtime_topology,
                        )
                    state.cached_tool_definitions = spec.tool_catalog.build_definitions(
                        desc_ctx
                    )

            tool_defs = state.cached_tool_definitions

            api_messages = [m.to_api_dict() for m in state.messages]

            llm_response: LLMResponse | None = None
            try:
                async for item in self._call_llm_streaming(
                    spec, api_messages, tool_defs, cancel_token=cancel_token
                ):
                    if item.llm_response is not None:
                        llm_response = item.llm_response
                    elif item.event is not None:
                        yield item
            except _KernelStopRequested:
                yield self._terminal(state, 'cancelled')
                return

            if llm_response is None:
                yield self._terminal(state, 'invalid_finish')
                return

            response = llm_response
            turn_usage = response.usage
            self._accumulate_usage(state.total_usage, response.usage)
            if spec.compactor:
                spec.compactor.update_message_count(len(state.messages))

            if not response.tool_calls:
                if not self._is_valid_natural_finish(response):
                    yield self._terminal(state, 'invalid_finish')
                    return
                state.messages.append(
                    AssistantMessage(
                        content=response.content,
                        reasoning_content=response.reasoning_content,
                    )
                )
                yield self._terminal(
                    state, 'natural', final_content=response.content
                )
                return

            assistant_msg = AssistantMessage(
                content=response.content,
                tool_calls=response.tool_calls,
                reasoning_content=response.reasoning_content,
            )
            state.messages.append(assistant_msg)

            if assistant_msg.tool_calls:
                yield _KernelItem(
                    event=AssistantStateEvent(
                        source="agent",
                        state=assistant_msg.model_dump(mode="json"),
                    )
                )

            for tc in response.tool_calls:
                yield _KernelItem(
                    event=ToolCallEvent(
                        source="agent",
                        call_id=tc.id,
                        tool_name=tc.name,
                        arguments=tc.arguments,
                    )
                )

            if spec.tool_runner is None:
                raise RuntimeError("No tool_runner in AgentRuntimeSpec")

            exec_ctx = ToolExecutionContext(
                turn=state.turn,
                max_turns=spec.max_turns,
                cancel_token=cancel_token,
            )
            runner_results = await spec.tool_runner.execute_batch(
                response.tool_calls, exec_ctx
            )

            for tc, tool_result in runner_results:
                state.messages.append(ToolMessage(
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    content=tool_result.content,
                ))
                yield _KernelItem(
                    event=ToolResultEvent(
                        source="agent",
                        call_id=tc.id,
                        tool_name=tc.name,
                        result=tool_result.content,
                        status=tool_result.status,
                        payload=tool_result.payload,
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

        yield self._terminal(state, 'max_turns')

    async def _call_llm_streaming(
        self,
        spec: AgentRuntimeSpec,
        api_messages: list[dict[str, Any]],
        tool_defs: list[dict[str, Any]] | None,
        *,
        cancel_token: CancellationToken | None = None,
    ) -> AsyncIterator[_KernelItem]:
        """Retry wrapper around _stream_llm_items with timeout-doubling retry on transient errors."""
        provider = spec.llm_provider
        current_timeout = getattr(provider, 'stream_timeout', None) or getattr(
            provider, '_timeout', 300.0
        )
        max_retries = getattr(provider, 'max_retries', 3)
        retry_delay = getattr(provider, 'retry_delay', 1.0)

        last_error: LLMError | None = None
        for attempt in range(max_retries):
            if cancel_token and cancel_token.is_cancelled:
                raise _KernelStopRequested()
            t0 = time.monotonic()
            try:
                # Collect all items to handle incomplete-response retry
                items: list[_KernelItem] = []
                async for item in self._stream_llm_items(
                    spec, api_messages, tool_defs,
                    timeout=current_timeout, cancel_token=cancel_token,
                ):
                    items.append(item)
                    # Yield event items immediately (streaming)
                    if item.event is not None:
                        yield item

                # Check for incomplete response
                final_items = [i for i in items if i.llm_response is not None]
                if final_items:
                    resp = final_items[0].llm_response
                    elapsed = time.monotonic() - t0
                    if self._is_incomplete_response(resp) and attempt < max_retries - 1:
                        logger.warning(
                            "LLM returned reasoning without content "
                            "(attempt %d/%d, elapsed=%.1fs), retrying.",
                            attempt + 1, max_retries, elapsed,
                        )
                        backoff = retry_delay * (2 ** attempt)
                        await self._sleep_backoff_with_cancel(backoff, cancel_token)
                        continue

                    if self._is_incomplete_response(resp):
                        logger.warning(
                            "LLM returned incomplete response after %d attempts, "
                            "returning degraded result.",
                            max_retries,
                        )
                        resp.degraded = True

                    yield _KernelItem(llm_response=resp)
                    return

            except LLMError as e:
                elapsed = time.monotonic() - t0
                if not e.retryable:
                    raise
                last_error = e
                current_timeout = current_timeout * 2
                backoff = retry_delay * (2 ** attempt) if attempt < max_retries - 1 else 0.0
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s (backoff=%.1fs)",
                    attempt + 1, max_retries, e, backoff,
                )
                if attempt < max_retries - 1:
                    await self._sleep_backoff_with_cancel(backoff, cancel_token)

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

    @staticmethod
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

    async def _stream_llm_items(
        self,
        spec: AgentRuntimeSpec,
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
        stream_id = f'turn-{len(api_messages)}'
        usage: dict[str, int] = {}
        producing_reasoning = False
        producing_content = False

        # Start marker
        yield _KernelItem(
            event=ThoughtEvent(
                source="agent",
                content="",
                stream_state="start",
                stream_id=stream_id,
            )
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
                    yield _KernelItem(
                        event=ThoughtEvent(
                            source="agent",
                            content=chunk.reasoning_content,
                            stream_state="streaming",
                            stream_id=stream_id,
                            reasoning_content=chunk.reasoning_content,
                        )
                    )

                if chunk.content:
                    yield _KernelItem(
                        event=ResponseEvent(
                            source="agent",
                            content=chunk.content,
                            stream_state="streaming",
                            stream_id=stream_id,
                        )
                    )

                # Accumulate parts (standard streaming accumulation)
                if chunk.reasoning_content:
                    reasoning_parts.append(chunk.reasoning_content)
                    producing_reasoning = True

                if chunk.content:
                    # Segment transition: reasoning -> content
                    if producing_reasoning:
                        yield _KernelItem(
                            event=ThoughtEvent(
                                source="agent",
                                content=''.join(reasoning_parts),
                                stream_state="complete",
                                stream_id=stream_id,
                                reasoning_content=''.join(reasoning_parts),
                            )
                        )
                        producing_reasoning = False
                    content_parts.append(chunk.content)
                    producing_content = True

                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
                if chunk.usage:
                    usage = chunk.usage
                if chunk.tool_call_deltas:
                    # Segment transition: reasoning -> tool_calls
                    if producing_reasoning:
                        yield _KernelItem(
                            event=ThoughtEvent(
                                source="agent",
                                content=''.join(reasoning_parts),
                                stream_state="complete",
                                stream_id=stream_id,
                                reasoning_content=''.join(reasoning_parts),
                            )
                        )
                        producing_reasoning = False
                    # Segment transition: content -> tool_calls
                    if producing_content:
                        yield _KernelItem(
                            event=ResponseEvent(
                                source="agent",
                                content=''.join(content_parts),
                                stream_state="complete",
                                stream_id=stream_id,
                            )
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
            # Emit segment-complete for any in-progress segments
            if producing_reasoning:
                yield _KernelItem(
                    event=ThoughtEvent(
                        source="agent",
                        content=''.join(reasoning_parts),
                        stream_state="complete",
                        stream_id=stream_id,
                        reasoning_content=''.join(reasoning_parts),
                    )
                )
            if producing_content:
                yield _KernelItem(
                    event=ResponseEvent(
                        source="agent",
                        content=''.join(content_parts),
                        stream_state="complete",
                        stream_id=stream_id,
                    )
                )
            # End marker
            yield _KernelItem(
                event=ResponseEvent(
                    source="agent",
                    content="",
                    stream_state="end",
                    stream_id=stream_id,
                )
            )

        if stream_cancelled:
            raise _KernelStopRequested()

        total_stream_ms = (time.perf_counter() - t_stream0) * 1000.0
        joined_content = ''.join(content_parts)
        joined_reasoning = ''.join(reasoning_parts)
        logger.info(
            'LLM stream timing (generator): stream_id=%s api_messages=%d chunks=%d '
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

        yield _KernelItem(
            llm_response=LLMResponse(
                content=joined_content or None,
                reasoning_content=joined_reasoning or None,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                usage=usage,
            )
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
