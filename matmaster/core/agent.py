"""AgentKernel -- pure execution loop for the agent kernel.

Consumes an AgentRuntimeSpec and executes the LLM -> guard -> hook -> tool
-> message accumulate -> loop cycle. All termination paths go through
_finish() which produces a KernelResult.

Termination conditions:
- natural: LLM returns no tool_calls
- max_turns: turn counter reaches spec.max_turns
- cancelled: stop_event is set externally
- hook_stopped: should_continue hook returns False
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from matmaster.core.guard_pipeline import GuardPipeline
from matmaster.tools.tool_result import ToolResult
from matmaster.types.errors import LLMError

if TYPE_CHECKING:
    from matmaster.types.runtime import AgentRuntimeSpec, KernelResult, KernelRunResult
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

# ---------------------------------------------------------------------------
# Async bridge -- used by the sync Kernel to call async providers / tools / hooks.
# A dedicated event loop (NOT running run_forever) is used via
# run_until_complete for sequential sync→async bridging.
# Removed in Phase 17 when the Kernel itself becomes async.
# ---------------------------------------------------------------------------
_bridge_loop = asyncio.new_event_loop()


def _sync_call_async(coro, loop: asyncio.AbstractEventLoop = _bridge_loop):
    """Bridge an async coroutine to a synchronous call."""
    return loop.run_until_complete(coro)


def _sync_iterate_async(async_iter, loop: asyncio.AbstractEventLoop):
    """Bridge async iterator to sync iterator.

    Temporary bridge for Phase 13-16 transition period.
    Will be removed in Phase 17 when Kernel becomes fully async.
    """
    try:
        while True:
            try:
                yield loop.run_until_complete(async_iter.__anext__())
            except StopAsyncIteration:
                break
    except GeneratorExit:
        # Caller abandoned iteration (e.g. break in for loop)
        pass




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
        # Create shared bridge loop for all async calls (temporary Phase 13-16)
        _bridge_loop = asyncio.new_event_loop()
        try:
            # Enter provider async context manager
            _bridge_loop.run_until_complete(spec.llm_provider.__aenter__())
            try:
                # Enter summary_provider if it's a separate instance (addresses review HIGH-1)
                _summary_provider = None
                if spec.compactor and hasattr(spec.compactor, '_summary_provider'):
                    sp = spec.compactor._summary_provider
                    if sp is not spec.llm_provider:
                        _summary_provider = sp
                        _bridge_loop.run_until_complete(sp.__aenter__())

                try:
                    return self._run_loop(spec, task, history, stop_event, _bridge_loop)
                finally:
                    # Exit summary_provider if separate
                    if _summary_provider is not None:
                        _bridge_loop.run_until_complete(
                            _summary_provider.__aexit__(None, None, None)
                        )
            finally:
                _bridge_loop.run_until_complete(
                    spec.llm_provider.__aexit__(None, None, None)
                )
        finally:
            _bridge_loop.close()

    def _run_loop(
        self,
        spec: AgentRuntimeSpec,
        task: str,
        history: list[Message] | None,
        stop_event: threading.Event | None,
        _bridge_loop: asyncio.AbstractEventLoop,
    ) -> KernelRunResult:
        """Internal execution loop with bridge loop for async calls."""
        messages: list[Message] = [
            SystemMessage(content=spec.system_prompt),
            *(history or []),
            UserMessage(content=task),
        ]
        guard_pipeline = GuardPipeline(spec.guards)

        # Inject bridge loop to hooks that need it (e.g. ConfirmationHook)
        for hook in spec.hooks:
            if hasattr(hook, "set_loop"):
                hook.set_loop(_bridge_loop)

        turn = 0
        if spec.compactor:
            spec.compactor.update_message_count(len(messages))
        last_usage: dict[str, int] = {}
        total_usage: dict[str, int] = {}
        last_stop_reason: str | None = None

        while turn < spec.max_turns:
            # External cancel check (before each turn)
            if stop_event and stop_event.is_set():
                return self._finish(spec, messages, "cancelled", num_turns=turn, stop_reason=last_stop_reason, usage=total_usage)

            turn += 1

            # pre_llm_call hook (observation, all hooks called)
            _sync_call_async(run_pre_llm_call(spec.hooks, messages, turn), _bridge_loop)

            # should_continue hook (intercepting, short-circuit)
            if not _sync_call_async(run_should_continue(spec.hooks, messages, turn), _bridge_loop):
                return self._finish(spec, messages, "hook_stopped", num_turns=turn - 1, stop_reason=last_stop_reason, usage=total_usage)

            # Context compaction check
            if spec.compactor:
                _sync_call_async(
                    spec.compactor.compact_if_needed(messages, last_usage, turn),
                    _bridge_loop,
                )

            # LLM call (streaming by default)
            response = self._call_llm(spec, messages, _bridge_loop=_bridge_loop)
            last_usage = response.usage
            self._accumulate_usage(total_usage, response.usage)
            last_stop_reason = response.finish_reason
            if spec.compactor:
                spec.compactor.update_message_count(len(messages))

            # Natural finish: no tool_calls
            if not response.tool_calls:
                if not self._is_valid_natural_finish(response):
                    return self._finish(spec, messages, "invalid_finish", num_turns=turn, stop_reason=last_stop_reason, usage=total_usage)
                messages.append(
                    AssistantMessage(
                        content=response.content,
                        reasoning_content=response.reasoning_content,
                    )
                )
                return self._finish(
                    spec,
                    messages,
                    "natural",
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
                # Guard evaluation (before hooks)
                guard_result = guard_pipeline.evaluate(tc, turn, spec.max_turns)
                if not guard_result.allowed:
                    # Blocked: notify hooks, then append ToolMessage error
                    _sync_call_async(run_guard_blocked(spec.hooks, tc, guard_result), _bridge_loop)
                    blocked_content = f"BLOCKED: {guard_result.reason}"
                    if guard_result.guidance:
                        blocked_content += f"\n{guard_result.guidance}"
                    messages.append(
                        ToolMessage(
                            tool_call_id=tc.id,
                            tool_name=tc.name,
                            content=blocked_content,
                        )
                    )
                    continue

                # pre_tool_call hook (intercepting, short-circuit)
                action = _sync_call_async(run_pre_tool_call(spec.hooks, tc), _bridge_loop)
                if action == HookAction.SKIP:
                    messages.append(
                        ToolMessage(
                            tool_call_id=tc.id,
                            tool_name=tc.name,
                            content="Tool call skipped by hook.",
                        )
                    )
                    continue

                # Tool execution (async registry bridged via _sync_call_async)
                try:
                    tool_result = _sync_call_async(
                        spec.tool_registry.execute(tc.name, tc.arguments),
                        _bridge_loop,
                    )
                except Exception as e:
                    tool_result = ToolResult(
                        status="error",
                        content=(
                            f"Error executing tool '{tc.name}': "
                            f"{type(e).__name__}: {e}"
                        ),
                    )
                    logger.exception("Tool execution failed: %s", tc.name)
                messages.append(
                    ToolMessage(
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                        content=tool_result.content,
                    )
                )

                # post_tool_call hook (observation, all hooks called)
                _sync_call_async(run_post_tool_call(spec.hooks, tc, tool_result), _bridge_loop)

        # max_turns exhausted
        return self._finish(spec, messages, "max_turns", num_turns=turn, stop_reason=last_stop_reason, usage=total_usage)

    def _call_llm(
        self,
        spec: AgentRuntimeSpec,
        messages: list[Message],
        *,
        _bridge_loop: asyncio.AbstractEventLoop | None = None,
    ) -> LLMResponse:
        """Call LLM with timeout-doubling retry on transient errors."""
        # Create a temporary loop if called directly (e.g., from tests)
        _owns_loop = False
        if _bridge_loop is None:
            _bridge_loop = asyncio.new_event_loop()
            _owns_loop = True

        provider = spec.llm_provider
        current_timeout = getattr(provider, "stream_timeout", None) or getattr(
            provider, "_timeout", 300.0
        )
        max_retries = getattr(provider, "max_retries", 3)
        retry_delay = getattr(provider, "retry_delay", 1.0)

        last_error: LLMError | None = None
        try:
            for attempt in range(max_retries):
                try:
                    response = self._do_stream_llm(spec, messages, timeout=current_timeout, _bridge_loop=_bridge_loop)
                    if (
                        self._is_incomplete_response(response)
                        and attempt < max_retries - 1
                    ):
                        logger.warning(
                            "LLM returned reasoning without content "
                            "(attempt %d/%d), retrying.",
                            attempt + 1,
                            max_retries,
                        )
                        backoff = retry_delay * (2**attempt)
                        time.sleep(backoff)
                        continue
                    return response
                except LLMError as e:
                    if not e.retryable:
                        raise
                    last_error = e
                    next_timeout = current_timeout * 2
                    logger.warning(
                        "LLM stream timed out after %.0fs (attempt %d/%d). "
                        "Retrying with timeout=%.0fs.",
                        current_timeout,
                        attempt + 1,
                        max_retries,
                        next_timeout,
                    )
                    current_timeout = next_timeout
                    if attempt < max_retries - 1:
                        backoff = retry_delay * (2**attempt)
                        time.sleep(backoff)

            raise RuntimeError(
                f"LLM stream failed after {max_retries} attempts"
            ) from last_error
        finally:
            if _owns_loop:
                _bridge_loop.close()

    def _do_stream_llm(
        self,
        spec: AgentRuntimeSpec,
        messages: list[Message],
        *,
        timeout: float | None = None,
        _bridge_loop: asyncio.AbstractEventLoop | None = None,
    ) -> LLMResponse:
        """Call LLM via streaming, accumulate chunks into LLMResponse."""
        api_messages = [m.to_api_dict() for m in messages]
        tool_defs = (
            spec.tool_registry.get_tool_definitions()
            if spec.tool_registry
            and hasattr(spec.tool_registry, "get_tool_definitions")
            else None
        )

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls_acc: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        stream_id = f"turn-{len(messages)}"
        usage: dict[str, int] = {}
        producing_reasoning = False
        producing_content = False

        _sync_call_async(run_on_stream_chunk(
            spec.hooks,
            StreamChunk(stream_state="start", stream_id=stream_id),
        ), _bridge_loop)
        try:
            for chunk in _sync_iterate_async(
                spec.llm_provider.chat_stream(api_messages, tool_defs, timeout=timeout),
                _bridge_loop,
            ):
                if chunk.content or chunk.reasoning_content:
                    _sync_call_async(run_on_stream_chunk(
                        spec.hooks,
                        chunk.model_copy(
                            update={
                                "stream_state": "streaming",
                                "stream_id": stream_id,
                            }
                        ),
                    ), _bridge_loop)

                if chunk.reasoning_content:
                    reasoning_parts.append(chunk.reasoning_content)
                    producing_reasoning = True

                if chunk.content:
                    if producing_reasoning:
                        _sync_call_async(run_on_segment_complete(
                            spec.hooks,
                            "thought",
                            "".join(reasoning_parts),
                            stream_id,
                        ), _bridge_loop)
                        producing_reasoning = False
                    content_parts.append(chunk.content)
                    producing_content = True

                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
                if chunk.usage:
                    usage = chunk.usage
                if chunk.tool_call_deltas:
                    if producing_reasoning:
                        _sync_call_async(run_on_segment_complete(
                            spec.hooks,
                            "thought",
                            "".join(reasoning_parts),
                            stream_id,
                        ), _bridge_loop)
                        producing_reasoning = False
                    if producing_content:
                        _sync_call_async(run_on_segment_complete(
                            spec.hooks,
                            "response",
                            "".join(content_parts),
                            stream_id,
                        ), _bridge_loop)
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
                            tool_calls_acc[idx]["name"] += delta["name"]
                        if delta.get("arguments"):
                            tool_calls_acc[idx]["arguments"] += delta["arguments"]
        finally:
            if producing_reasoning:
                _sync_call_async(run_on_segment_complete(
                    spec.hooks,
                    "thought",
                    "".join(reasoning_parts),
                    stream_id,
                ), _bridge_loop)
            if producing_content:
                _sync_call_async(run_on_segment_complete(
                    spec.hooks,
                    "response",
                    "".join(content_parts),
                    stream_id,
                ), _bridge_loop)
            _sync_call_async(run_on_stream_chunk(
                spec.hooks,
                StreamChunk(stream_state="end", stream_id=stream_id),
            ), _bridge_loop)

        # Assemble tool_calls from accumulated deltas
        tool_calls: list[ToolCallData] | None = None
        if tool_calls_acc:
            tool_calls = []
            for _, v in sorted(tool_calls_acc.items()):
                args = self._parse_arguments(v["arguments"])
                tool_calls.append(
                    ToolCallData(id=v["id"], name=v["name"], arguments=args)
                )

        return LLMResponse(
            content="".join(content_parts) or None,
            reasoning_content="".join(reasoning_parts) or None,
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
            logger.warning("Failed to parse tool call arguments: %s", raw[:200])
            return {"_raw": raw}

    @staticmethod
    def _is_valid_natural_finish(response: LLMResponse) -> bool:
        """Only commit a natural finish when the stream terminates cleanly."""
        return not response.tool_calls and response.finish_reason == "stop"

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
        if reason == "cancelled":
            status = "cancelled"
        elif reason == "invalid_finish":
            status = "failed"
        else:
            status = "completed"
        from matmaster.types.runtime import KernelResult, KernelRunResult  # lazy to avoid circular

        result = KernelResult(
            status=status,
            reason=reason,
            final_content=final_content,
            num_turns=num_turns,
            stop_reason=stop_reason,
            usage=dict(usage) if usage else {},
        )
        return KernelRunResult(result=result, messages=list(messages))
