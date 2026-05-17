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
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from matmaster.core.agent_compaction import (
    run_preflight_compaction_if_needed,
    run_runtime_compaction_if_needed,
)
from matmaster.core.agent_llm_stream import call_llm_streaming
from matmaster.core.agent_tool_dispatch import (
    accumulate_usage,
    dispatch_tool_calls,
    validate_tool_call_ids,
)
from matmaster.core.finish_diagnostics import (
    build_finish_detail,
    is_valid_natural_finish,
)
from matmaster.core.kernel_items import (
    _KernelItem,
    _KernelState,
    _KernelStopRequested,
    _TerminalItem,
)
from matmaster.types.cancellation import CancellationToken
from matmaster.types.events import (
    AssistantStateEvent,
    FinishDetail,
    ResponseEvent,
    ToolCallEvent,
)

if TYPE_CHECKING:
    from matmaster.types.runtime import AgentRuntimeSpec

from matmaster.core.hooks import (
    HookEvent,
    RunContext,
    UserPromptContext,
)
from matmaster.response_text import (
    is_trivial_response_text,
)
from matmaster.types.messages import (
    AssistantMessage,
    ImageContentPart,
    LLMResponse,
    Message,
    SystemMessage,
    UserMessage,
)

logger = logging.getLogger(__name__)


_TERMINAL_REASON_TO_STATUS: dict[str, str] = {
    "cancelled": "cancelled",
    "invalid_finish": "failed",
    "natural": "completed",
    "max_turns": "completed",
}


def ensure_tool_definitions(
    spec: AgentRuntimeSpec,
    state: _KernelState,
) -> list[dict[str, Any]] | None:
    """Resolve and cache tool definitions on kernel state."""
    if spec.tool_catalog is None:
        return None

    if spec.tool_catalog.version != state.last_catalog_version:
        state.cached_tool_definitions = None
        state.last_catalog_version = spec.tool_catalog.version

    if state.cached_tool_definitions is None:
        from matmaster.types.tool_desc_ctx import ToolDescriptionContext

        desc_ctx = None
        if spec.runtime_topology is not None:
            desc_ctx = ToolDescriptionContext(
                session_kind=spec.runtime_topology.session_kind,
                workspace_root=spec.runtime_topology.workspace_root,
                topology=spec.runtime_topology,
            )
        state.cached_tool_definitions = spec.tool_catalog.build_definitions(desc_ctx)
    return state.cached_tool_definitions


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
        Items with event=None (llm_response) are consumed internally and
        not yielded.
        """
        from matmaster.types.events import RunResultEvent

        async with spec.llm_provider:
            last_reason: str | None = None

            async def _consume_and_yield():
                nonlocal last_reason
                async for item in self._run_items(spec, task, history, cancel_token):
                    if item.terminal is not None:
                        reason = item.terminal.reason
                        last_reason = reason
                        status = _TERMINAL_REASON_TO_STATUS.get(reason, "completed")
                        yield RunResultEvent(
                            source="agent",
                            status=status,
                            reason=reason,
                            final_content=item.terminal.final_content,
                            num_turns=item.terminal.num_turns,
                            usage=item.terminal.usage,
                            usage_vendor_by_turn=item.terminal.usage_vendor_by_turn,
                            messages=item.terminal.messages,
                            finish_detail=item.terminal.finish_detail,
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
                async for event in _consume_and_yield():
                    yield event
            except BaseException as exc:
                if last_reason is None:
                    if isinstance(exc, (GeneratorExit, asyncio.CancelledError)):
                        last_reason = "cancelled"
                    else:
                        last_reason = "error"
                raise
            finally:
                if spec.hook_executor is not None:
                    await spec.hook_executor.emit(
                        HookEvent.RUN_END,
                        RunContext(
                            task_id=spec.meta.get("task_id", ""),
                            session_id=spec.meta.get("session_id", ""),
                            reason=last_reason or "error",
                        ),
                    )

    @staticmethod
    def _terminal(
        state: _KernelState,
        reason: str,
        *,
        final_content: str | None = None,
        turn_offset: int = 0,
        finish_detail: FinishDetail | None = None,
    ) -> _KernelItem:
        return _KernelItem(
            terminal=_TerminalItem(
                reason=reason,
                final_content=final_content,
                num_turns=state.turn + turn_offset,
                usage=dict(state.total_usage),
                usage_vendor_by_turn=[
                    dict(item) for item in state.usage_vendor_by_turn
                ],
                messages=list(state.messages),
                finish_detail=finish_detail,
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

        from matmaster.context.sources.turn_input import TurnInput

        raw_turn_input = spec.meta.get("turn_input")
        turn_input = (
            raw_turn_input
            if isinstance(raw_turn_input, TurnInput)
            else TurnInput.from_payload(raw_turn_input)
        )
        current_user_images = [
            ImageContentPart.model_validate(image)
            for image in spec.meta.get("current_user_images", [])
        ]
        state = _KernelState(
            messages=[
                SystemMessage(content=spec.system_prompt),
                *(history or []),
                UserMessage(content=task, images=current_user_images),
            ]
        )

        checkpoint_sink = spec.runtime_ports.checkpoint_sink
        tool_definitions = ensure_tool_definitions(spec, state)

        async for item in run_preflight_compaction_if_needed(
            spec=spec,
            state=state,
            history=history,
            turn_input=turn_input,
            checkpoint_sink=checkpoint_sink,
            tool_definitions=tool_definitions,
        ):
            yield item

        turn_usage: dict[str, int] = {}

        while state.turn < spec.max_turns:
            if cancel_token and cancel_token.is_cancelled:
                yield self._terminal(state, "cancelled")
                return

            state.turn += 1
            tool_definitions = ensure_tool_definitions(spec, state)

            async for item in run_runtime_compaction_if_needed(
                spec=spec,
                state=state,
                turn_usage=turn_usage,
                checkpoint_sink=checkpoint_sink,
                tool_definitions=tool_definitions,
            ):
                yield item

            tool_defs = tool_definitions

            api_messages = state.pipeline.feed_tail(state.messages)

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
                yield self._terminal(state, "cancelled")
                return

            if llm_response is None:
                yield self._terminal(
                    state,
                    "invalid_finish",
                    finish_detail=build_finish_detail(None),
                )
                return

            response = llm_response
            turn_usage = response.usage
            accumulate_usage(state.total_usage, response.usage)
            state.usage_vendor_by_turn.append(
                dict(response.usage_vendor) if response.usage_vendor else {}
            )
            turn_index = state.turn - 1
            is_root_run = spec.meta.get("spawn_id") is None
            if (
                is_root_run
                and response.content
                and not is_trivial_response_text(response.content)
            ):
                yield _KernelItem(
                    event=ResponseEvent(
                        source="agent",
                        content=response.content,
                        stream_state="complete",
                        turn_index=turn_index,
                        turn_usage=turn_usage,
                        total_usage=state.total_usage,
                        usage_vendor=response.usage_vendor or None,
                    )
                )
            if spec.compactor:
                spec.compactor.update_message_count(len(state.messages))

            if response.tool_calls:
                validate_tool_call_ids(response.tool_calls)

            if not response.tool_calls:
                if not is_valid_natural_finish(response):
                    yield self._terminal(
                        state,
                        "invalid_finish",
                        finish_detail=build_finish_detail(response),
                    )
                    return
                state.messages.append(
                    AssistantMessage(
                        content=response.content,
                        reasoning_content=response.reasoning_content,
                    )
                )
                yield self._terminal(state, "natural", final_content=response.content)
                return

            assistant_msg = AssistantMessage(
                content=response.content,
                tool_calls=response.tool_calls,
                reasoning_content=response.reasoning_content,
            )
            assistant_finish_detail = None
            if response.finish_reason == "length":
                assistant_finish_detail = build_finish_detail(response)
                logger.warning(
                    "tool call response ended with length finish reason",
                    extra={
                        "turn": state.turn,
                        "tool_names": [tc.name for tc in response.tool_calls or []],
                        "finish_detail": assistant_finish_detail.model_dump(
                            mode="json"
                        ),
                    },
                )
            state.messages.append(assistant_msg)

            if assistant_msg.tool_calls:
                yield _KernelItem(
                    event=AssistantStateEvent(
                        source="agent",
                        state=assistant_msg.model_dump(mode="json"),
                        turn_index=turn_index,
                        turn_usage=turn_usage,
                        total_usage=state.total_usage,
                        finish_detail=assistant_finish_detail,
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

            async for item in dispatch_tool_calls(
                spec=spec,
                state=state,
                tool_calls=response.tool_calls,
                turn_usage=turn_usage,
                turn_index=turn_index,
                cancel_token=cancel_token,
            ):
                yield item

        yield self._terminal(state, "max_turns")

    async def _call_llm_streaming(
        self,
        spec: AgentRuntimeSpec,
        api_messages: list[dict[str, Any]],
        tool_defs: list[dict[str, Any]] | None,
        *,
        cancel_token: CancellationToken | None = None,
    ) -> AsyncIterator[_KernelItem]:
        """Indirection point so tests can monkey-patch the streaming call."""
        async for item in call_llm_streaming(
            spec,
            api_messages,
            tool_defs,
            cancel_token=cancel_token,
        ):
            yield item
